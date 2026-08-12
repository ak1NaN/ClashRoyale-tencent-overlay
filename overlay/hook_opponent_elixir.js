'use strict';

const LIB_NAME = 'libg.so';
const GET_MANA = 0x0117af8c;
const DEPLOY_GET_MANA_RETURN = 0x00f33e90;
const RESOLVE_PLAYER = 0x00ef2aa8;
const LOCAL_RESOLVE_RETURN = 0x00e8cdf4;
const LOCAL_IDENTITY_HOLDER = 0x01ba3ec8;
const PLAYER_MANA_RAW = 0x300;
const SAMPLE_INTERVAL_MS = 100;
const RECORD_CONSTRUCTOR = 0x00f33bbc;
const QUEUE_DELAY_MS = 8;
const QUEUE_DEDUPE_MS = 3000;
const CARD_ID_MIN = 26000000;
const CARD_ID_MAX = 28000018;

const players = new Map();
const seenQueueEvents = new Map();
let localPlayer = null;
let localIdentity = null;
let activeBattleState = null;
let activeOpponentState = null;
let localTeamSide = null;

function nowMs() {
  return Date.now();
}

function emit(payload) {
  payload.t_ms = nowMs();
  send(payload);
}

function readS32(base, offset) {
  try {
    return base.add(offset).readS32();
  } catch (_) {
    return null;
  }
}

function readPointer(base, offset) {
  try {
    return base.add(offset).readPointer();
  } catch (_) {
    return ptr(0);
  }
}

function tileCenter(value) {
  return Number.isInteger(value) ? Math.round((value - 500) / 1000) + 0.5 : null;
}

function emitQueueRecord(record) {
  const action = readPointer(record, 0x38);
  if (action.isNull()) return;
  const cardId = readS32(action, 0x40);
  const x = readS32(record, 0x28);
  const y = readS32(record, 0x2c);
  const ownerKey0 = readS32(record, 0x14);
  const ownerKey1 = readS32(record, 0x18);
  if (!Number.isInteger(cardId) || cardId < CARD_ID_MIN || cardId > CARD_ID_MAX) return;

  const tileX = tileCenter(x);
  const tileY = tileCenter(y);
  if (tileX === null || tileY === null) return;

  const dedupeKey = action.toString() + ':' + cardId + ':' + x + ':' + y;
  const now = nowMs();
  const previous = seenQueueEvents.get(dedupeKey);
  if (previous !== undefined && now - previous <= QUEUE_DEDUPE_MS) return;
  seenQueueEvents.set(dedupeKey, now);

  const ownerKey = Number.isInteger(ownerKey0) && Number.isInteger(ownerKey1)
    ? ownerKey0 + ':' + ownerKey1
    : null;
  const side = localIdentity === null || ownerKey === null
    ? 'unknown'
    : ownerKey === localIdentity ? 'self' : 'opponent';

  emit({
    event: 'queue_deploy',
    battle_clock: null,
    source: 'tencent_elixir_overlay_constructor',
    action_ptr: action.toString(),
    card_id: cardId,
    owner_key: ownerKey,
    side: side,
    target_raw: { x: x, y: y },
    target_tile_center: { x: tileX, y: tileY }
  });
}

function manaSnapshot(player) {
  const raw = readS32(player, PLAYER_MANA_RAW);
  if (raw === null) return null;
  return {
    mana_raw: raw,
    mana: raw / 10000.0
  };
}

function sideFor(player, identity) {
  if (localIdentity !== null && identity !== null) {
    return identity === localIdentity ? 'self' : 'opponent';
  }
  if (localPlayer !== null) {
    return player.equals(localPlayer) ? 'self' : 'opponent';
  }
  return 'unknown';
}

function resolvePlayerIndex(battleState, key0, key1) {
  if (battleState.isNull() || !Number.isInteger(key0) || !Number.isInteger(key1)) return null;
  const count = readS32(battleState, 0x60);
  if (!Number.isInteger(count) || count < 1 || count > 8) return null;

  for (let index = 0; index < count; index += 1) {
    const keyObject = readPointer(battleState, 0x30 + index * 8);
    if (keyObject.isNull()) continue;
    if (readS32(keyObject, 0) === key0 && readS32(keyObject, 4) === key1) return index;
  }
  return null;
}

function publishBattleContext() {
  if (activeBattleState === null) return;
  const parts = localIdentity === null ? [] : localIdentity.split(':');
  const key0 = parts.length === 2 ? Number(parts[0]) : null;
  const key1 = parts.length === 2 ? Number(parts[1]) : null;
  const localIndex = resolvePlayerIndex(activeBattleState, key0, key1);
  localTeamSide = localIndex === null ? null : localIndex & 1;

  emit({
    event: 'elixir_battle_changed',
    battle_state: activeBattleState.toString(),
    local_key: localIdentity,
    local_player_index: localIndex,
    local_team_side: localTeamSide,
    auto_flip: localTeamSide === null ? null : localTeamSide === 0
  });
}

function selectBattle(battleState) {
  if (activeBattleState !== null && activeBattleState.equals(battleState)) return false;
  activeBattleState = ptr(battleState.toString());
  activeOpponentState = null;
  localPlayer = null;
  players.clear();
  publishBattleContext();
  return true;
}

function publishSideUpdates() {
  players.forEach(function (state) {
    const side = sideFor(state.player, state.identity);
    if (side === state.side) return;
    state.side = side;
    emit({
      event: 'elixir_side_update',
      player_key: state.identity,
      player: state.player.toString(),
      side: side
    });
  });
}

function rememberPlayer(record, player) {
  const key0 = readS32(record, 0x14);
  const key1 = readS32(record, 0x18);
  if (key0 === null || key1 === null) return null;

  const identity = key0 + ':' + key1;
  let state = players.get(identity);
  if (state === undefined || !state.player.equals(player)) {
    state = {
      identity: identity,
      key0: key0,
      key1: key1,
      player: player,
      side: sideFor(player, identity),
      lastRaw: null
    };
    players.set(identity, state);
    emit({
      event: 'elixir_player_discovered',
      player_key: identity,
      key0: key0,
      key1: key1,
      player: player.toString(),
      side: state.side
    });
  }
  return state;
}

function install() {
  const module = Process.findModuleByName(LIB_NAME);
  if (module === null) {
    setTimeout(install, 250);
    return;
  }

  const getMana = module.base.add(GET_MANA);
  const deployReturn = module.base.add(DEPLOY_GET_MANA_RETURN);
  const resolvePlayer = module.base.add(RESOLVE_PLAYER);
  const localResolveReturn = module.base.add(LOCAL_RESOLVE_RETURN);
  const localIdentityHolder = module.base.add(LOCAL_IDENTITY_HOLDER);

  Interceptor.attach(module.base.add(RECORD_CONSTRUCTOR), {
    onEnter(args) {
      this.record = args[0];
    },

    onLeave() {
      const record = ptr(this.record.toString());
      setTimeout(function () {
        emitQueueRecord(record);
      }, QUEUE_DELAY_MS);
    }
  });

  const identityTimer = setInterval(function () {
    try {
      const holder = localIdentityHolder.readPointer();
      const identityObject = holder.readPointer();
      const key0 = identityObject.add(0x20).readS32();
      const key1 = identityObject.add(0x24).readS32();
      const identity = key0 + ':' + key1;
      if (key0 === 0 && key1 === 0) return;
      localIdentity = identity;
      clearInterval(identityTimer);
      emit({
        event: 'local_elixir_identity_resolved',
        player_key: identity,
        key0: key0,
        key1: key1,
        identity_object: identityObject.toString(),
        side: 'self'
      });
      if (activeBattleState !== null && localTeamSide === null) publishBattleContext();
      publishSideUpdates();
    } catch (_) {
      // The identity object is populated after the game session is ready.
    }
  }, 250);

  Interceptor.attach(resolvePlayer, {
    onEnter(args) {
      this.accepted = this.returnAddress.equals(localResolveReturn);
      if (!this.accepted) return;
      this.key0 = args[1].toInt32();
      this.key1 = args[2].toInt32();
    },

    onLeave(retval) {
      if (!this.accepted || retval.isNull()) return;
      const resolved = ptr(retval.toString());
      if (localPlayer !== null && localPlayer.equals(resolved)) return;
      localPlayer = resolved;
      localIdentity = this.key0 + ':' + this.key1;
      emit({
        event: 'local_elixir_player_resolved',
        player: localPlayer.toString(),
        key0: this.key0,
        key1: this.key1,
        side: 'self'
      });
      if (activeBattleState !== null && localTeamSide === null) publishBattleContext();
      publishSideUpdates();
    }
  });

  Interceptor.attach(getMana, {
    onEnter(args) {
      this.accepted = this.returnAddress.equals(deployReturn);
      if (!this.accepted) return;

      this.player = args[0];
      this.record = this.context.x19;
      this.battleState = this.context.x24;
      selectBattle(this.battleState);
      const state = rememberPlayer(this.record, this.player);
      this.identity = state === null ? null : state.identity;
      const ownerKey0 = readS32(this.record, 0x14);
      const ownerKey1 = readS32(this.record, 0x18);
      const ownerIndex = resolvePlayerIndex(this.battleState, ownerKey0, ownerKey1);
      const ownerTeamSide = ownerIndex === null ? null : ownerIndex & 1;
      const side = this.identity !== null && localIdentity !== null
        ? this.identity === localIdentity ? 'self' : 'opponent'
        : 'unknown';
      if (state !== null) {
        state.battleState = ptr(this.battleState.toString());
        state.teamSide = ownerTeamSide;
        state.side = side;
        state.lastRaw = null;
        if (side === 'self') localPlayer = this.player;
        if (side === 'opponent') activeOpponentState = state;
      }
      const action = readPointer(this.record, 0x38);
      const snapshot = manaSnapshot(this.player);

      emit({
        event: 'deploy_elixir_before',
        player_key: state === null ? null : state.identity,
        side: side,
        battle_state: this.battleState.toString(),
        owner_player_index: ownerIndex,
        owner_team_side: ownerTeamSide,
        local_team_side: localTeamSide,
        player: this.player.toString(),
        record: this.record.toString(),
        action: action.toString(),
        card_id: action.isNull() ? null : readS32(action, 0x40),
        target_x: readS32(this.record, 0x28),
        target_y: readS32(this.record, 0x2c),
        mana_raw: snapshot === null ? null : snapshot.mana_raw,
        mana: snapshot === null ? null : snapshot.mana
      });
    },

    onLeave(retval) {
      if (!this.accepted) return;
      emit({
        event: 'deploy_elixir_getter_result',
        side: this.identity !== null && localIdentity !== null && this.identity === localIdentity ? 'self' : 'opponent',
        player: this.player.toString(),
        mana_floor: retval.toInt32()
      });
    }
  });

  setInterval(function () {
    const state = activeOpponentState;
    if (state === null || activeBattleState === null) return;
    if (state.battleState === undefined || !state.battleState.equals(activeBattleState)) return;
    const snapshot = manaSnapshot(state.player);
    if (snapshot === null || snapshot.mana_raw === state.lastRaw) return;
    state.lastRaw = snapshot.mana_raw;
    emit({
      event: 'elixir_sample',
      battle_state: activeBattleState.toString(),
      player_key: state.identity,
      side: 'opponent',
      team_side: state.teamSide,
      player: state.player.toString(),
      mana_raw: snapshot.mana_raw,
      mana: snapshot.mana
    });
  }, SAMPLE_INTERVAL_MS);

  setInterval(function () {
    const now = nowMs();
    seenQueueEvents.forEach(function (timestamp, key) {
      if (now - timestamp > QUEUE_DEDUPE_MS) seenQueueEvents.delete(key);
    });
  }, QUEUE_DEDUPE_MS);

  emit({
    event: 'opponent_elixir_probe_ready',
    lib: LIB_NAME,
    base: module.base.toString(),
    get_mana: getMana.toString(),
    deploy_return: deployReturn.toString(),
    resolve_player: resolvePlayer.toString(),
    local_resolve_return: localResolveReturn.toString(),
    local_identity_holder: localIdentityHolder.toString(),
    queue_constructor: module.base.add(RECORD_CONSTRUCTOR).toString(),
    player_mana_offset: '0x300',
    sample_interval_ms: SAMPLE_INTERVAL_MS
  });
}

install();
