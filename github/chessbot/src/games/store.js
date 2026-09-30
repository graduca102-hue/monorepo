import { randomBytes, randomInt } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { applyChessMove, chessForClient, createChessState, legalChessMoves } from "./chess.js";
import {
  applyCheckersMove,
  checkersBoardForClient,
  createCheckersState,
  legalCheckersMoves,
} from "./checkers.js";
import { GameError } from "./errors.js";

const DAY = 24 * 60 * 60 * 1000;
const otherColor = (color) => (color === "white" ? "black" : "white");

function publicPlayer(player) {
  if (!player) return null;
  return {
    id: player.id,
    firstName: player.firstName,
    lastName: player.lastName,
    username: player.username,
    photoUrl: player.photoUrl,
  };
}

export class GameStore {
  constructor(filePath) {
    this.filePath = filePath;
    this.games = new Map();
    this.load();
  }

  load() {
    try {
      const payload = JSON.parse(fs.readFileSync(this.filePath, "utf8"));
      for (const game of Object.values(payload.games || {})) this.games.set(game.id, game);
      this.cleanup(false);
    } catch (error) {
      if (error.code !== "ENOENT") throw error;
    }
  }

  persist() {
    fs.mkdirSync(path.dirname(this.filePath), { recursive: true });
    const temporary = `${this.filePath}.${process.pid}.tmp`;
    const games = Object.fromEntries(this.games);
    fs.writeFileSync(temporary, JSON.stringify({ version: 1, games }, null, 2));
    fs.renameSync(temporary, this.filePath);
  }

  cleanup(save = true) {
    const now = Date.now();
    let changed = false;
    for (const [id, game] of this.games) {
      const age = now - new Date(game.updatedAt || game.createdAt).getTime();
      const expired = game.status === "waiting" ? age > DAY : game.status === "finished" && age > 30 * DAY;
      if (expired) {
        this.games.delete(id);
        changed = true;
      }
    }
    if (changed && save) this.persist();
  }

  create(type, host) {
    if (!new Set(["chess", "checkers"]).has(type)) {
      throw new GameError("Неизвестный тип игры", 400, "UNKNOWN_GAME");
    }
    this.cleanup(false);
    const now = new Date().toISOString();
    const game = {
      id: randomBytes(9).toString("base64url"),
      type,
      status: "waiting",
      host,
      players: { white: null, black: null },
      position: type === "chess" ? createChessState() : createCheckersState(),
      result: null,
      inlineMessageId: null,
      createdAt: now,
      updatedAt: now,
    };
    this.games.set(game.id, game);
    this.persist();
    return game;
  }

  get(id) {
    return this.games.get(id) || null;
  }

  list() {
    return [...this.games.values()];
  }

  require(id) {
    const game = this.get(id);
    if (!game) throw new GameError("Партия не найдена или устарела", 404, "GAME_NOT_FOUND");
    return game;
  }

  attachInlineMessage(id, inlineMessageId) {
    const game = this.require(id);
    if (inlineMessageId && game.inlineMessageId !== inlineMessageId) {
      game.inlineMessageId = inlineMessageId;
      game.updatedAt = new Date().toISOString();
      this.persist();
    }
    return game;
  }

  join(id, user, inlineMessageId = null) {
    const game = this.require(id);
    if (inlineMessageId) game.inlineMessageId = inlineMessageId;

    if (game.host.id === user.id) {
      if (game.status === "waiting") {
        throw new GameError("Это ваша партия — дождитесь соперника", 409, "HOST_CANNOT_JOIN");
      }
      return game;
    }

    if (Object.values(game.players).some((player) => player?.id === user.id)) return game;
    if (game.status !== "waiting") {
      throw new GameError("В этой партии уже играют двое", 409, "GAME_FULL");
    }

    const hostIsWhite = randomInt(2) === 0;
    game.players.white = hostIsWhite ? game.host : user;
    game.players.black = hostIsWhite ? user : game.host;
    game.status = "playing";
    game.updatedAt = new Date().toISOString();
    this.persist();
    return game;
  }

  colorFor(game, userId) {
    if (game.players.white?.id === userId) return "white";
    if (game.players.black?.id === userId) return "black";
    return null;
  }

  assertCanOpen(game, userId) {
    if (game.status === "waiting" && game.host.id === userId) return;
    if (this.colorFor(game, userId)) return;
    throw new GameError("Эта партия доступна только её участникам", 403, "NOT_A_PLAYER");
  }

  move(id, userId, move) {
    const game = this.require(id);
    if (game.status !== "playing") {
      throw new GameError("Партия ещё не началась или уже завершена", 409, "GAME_NOT_ACTIVE");
    }
    const color = this.colorFor(game, userId);
    if (!color) throw new GameError("Вы не участвуете в этой партии", 403, "NOT_A_PLAYER");

    const outcome = game.type === "chess"
      ? applyChessMove(game.position, color, move)
      : applyCheckersMove(game.position, color, move);

    if (outcome.finished) {
      game.status = "finished";
      game.result = outcome.result;
      game.finishedAt = new Date().toISOString();
    }
    game.updatedAt = new Date().toISOString();
    this.persist();
    return game;
  }

  resign(id, userId) {
    const game = this.require(id);
    if (game.status !== "playing") throw new GameError("Партия уже завершена", 409, "GAME_NOT_ACTIVE");
    const color = this.colorFor(game, userId);
    if (!color) throw new GameError("Вы не участвуете в этой партии", 403, "NOT_A_PLAYER");
    game.status = "finished";
    game.result = { winner: otherColor(color), reason: "Соперник сдался" };
    game.finishedAt = new Date().toISOString();
    game.updatedAt = game.finishedAt;
    this.persist();
    return game;
  }

  viewFor(gameOrId, userId) {
    const game = typeof gameOrId === "string" ? this.require(gameOrId) : gameOrId;
    this.assertCanOpen(game, userId);
    const yourColor = this.colorFor(game, userId);
    let board = {};
    let turn = "white";
    let inCheck = false;
    let history = [];
    let legalMoves = [];

    if (game.type === "chess") {
      const position = chessForClient(game.position);
      board = position.pieces;
      turn = position.turn;
      inCheck = position.inCheck;
      history = position.history;
      if (game.status === "playing" && yourColor === turn) legalMoves = legalChessMoves(game.position);
    } else {
      board = checkersBoardForClient(game.position);
      turn = game.position.turn;
      history = game.position.history.map((entry) => entry.notation);
      if (game.status === "playing" && yourColor === turn) legalMoves = legalCheckersMoves(game.position);
    }

    return {
      id: game.id,
      type: game.type,
      status: game.status,
      host: publicPlayer(game.host),
      players: {
        white: publicPlayer(game.players.white),
        black: publicPlayer(game.players.black),
      },
      yourColor,
      turn,
      inCheck,
      board,
      legalMoves,
      forcedPiece: game.position.forcedPiece || null,
      lastMove: game.position.lastMove || null,
      history,
      result: game.result,
      updatedAt: game.updatedAt,
    };
  }
}
