import { Chess } from "chess.js";
import { GameError } from "./errors.js";

const colorName = (short) => (short === "w" ? "white" : "black");

export function createChessState() {
  const chess = new Chess();
  return {
    fen: chess.fen(),
    moves: [],
    lastMove: null,
  };
}

function restoreChess(state) {
  const chess = new Chess();
  try {
    for (const move of state.moves || []) {
      chess.move({ from: move.from, to: move.to, promotion: move.promotion || undefined });
    }
    return chess;
  } catch {
    // Older persisted games can still be opened even if they only contain FEN.
    return new Chess(state.fen);
  }
}

export function legalChessMoves(state) {
  const chess = restoreChess(state);
  return chess.moves({ verbose: true }).map((move) => ({
    from: move.from,
    to: move.to,
    promotion: move.promotion || null,
    capture: Boolean(move.captured),
  }));
}

function resultFromPosition(chess, movedColor) {
  if (chess.isCheckmate()) {
    return { winner: movedColor, reason: "Мат" };
  }
  if (chess.isStalemate()) return { winner: null, reason: "Пат" };
  if (chess.isThreefoldRepetition()) return { winner: null, reason: "Троекратное повторение" };
  if (chess.isInsufficientMaterial()) return { winner: null, reason: "Недостаточно материала" };
  if (chess.isDrawByFiftyMoves()) return { winner: null, reason: "Правило 50 ходов" };
  if (chess.isDraw()) return { winner: null, reason: "Ничья" };
  return null;
}

export function applyChessMove(state, color, requestedMove) {
  const chess = restoreChess(state);
  if (colorName(chess.turn()) !== color) {
    throw new GameError("Сейчас ход соперника", 409, "NOT_YOUR_TURN");
  }

  let move;
  try {
    move = chess.move({
      from: requestedMove.from,
      to: requestedMove.to,
      promotion: requestedMove.promotion || "q",
    });
  } catch {
    throw new GameError("Недопустимый ход", 422, "ILLEGAL_MOVE");
  }
  if (!move) throw new GameError("Недопустимый ход", 422, "ILLEGAL_MOVE");

  state.moves.push({
    from: move.from,
    to: move.to,
    promotion: move.promotion || null,
    san: move.san,
  });
  state.fen = chess.fen();
  state.lastMove = { from: move.from, to: move.to };

  return {
    finished: chess.isGameOver(),
    result: resultFromPosition(chess, color),
  };
}

export function chessForClient(state) {
  const chess = restoreChess(state);
  const pieces = {};
  for (const row of chess.board()) {
    for (const piece of row) {
      if (!piece) continue;
      pieces[piece.square] = `${piece.color}${piece.type}`;
    }
  }
  return {
    pieces,
    turn: colorName(chess.turn()),
    inCheck: chess.inCheck(),
    history: (state.moves || []).map((move) => move.san),
  };
}
