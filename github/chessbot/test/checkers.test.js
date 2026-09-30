import test from "node:test";
import assert from "node:assert/strict";
import {
  applyCheckersMove,
  createCheckersState,
  fromSquare,
  legalCheckersMoves,
} from "../src/games/checkers.js";

function emptyState() {
  return {
    board: Array.from({ length: 8 }, () => Array(8).fill(null)),
    turn: "white",
    forcedPiece: null,
    capturedInTurn: [],
    lastMove: null,
    history: [],
  };
}

function put(state, square, piece) {
  const { row, col } = fromSquare(square);
  state.board[row][col] = piece;
}

test("initial Russian checkers position has seven legal moves", () => {
  const state = createCheckersState();
  assert.equal(legalCheckersMoves(state).length, 7);
});

test("capture is mandatory and men capture backwards", () => {
  const state = emptyState();
  put(state, "c3", "w");
  put(state, "d4", "b");
  put(state, "g3", "w");

  assert.deepEqual(legalCheckersMoves(state), [
    { from: "c3", to: "e5", capture: "d4" },
  ]);
});

test("a man promotes during a capture chain and continues as a flying king", () => {
  const state = emptyState();
  put(state, "b6", "w");
  put(state, "c7", "b");
  put(state, "e7", "b");

  const first = applyCheckersMove(state, "white", { from: "b6", to: "d8" });
  assert.equal(first.continueCapture, true);
  assert.equal(state.forcedPiece, "d8");
  assert.equal(state.board[0][3], "W");
  assert.deepEqual(
    legalCheckersMoves(state).map((move) => move.to),
    ["f6", "g5", "h4"],
  );

  const second = applyCheckersMove(state, "white", { from: "d8", to: "f6" });
  assert.equal(second.finished, true);
  assert.equal(second.result.winner, "white");
});

test("flying king may land on any empty square after one captured piece", () => {
  const state = emptyState();
  put(state, "b2", "W");
  put(state, "d4", "b");
  const moves = legalCheckersMoves(state);
  assert.deepEqual(moves.map((move) => move.to), ["e5", "f6", "g7", "h8"]);
});

test("Turkish strike keeps captured pieces as blockers until the sequence ends", () => {
  const state = emptyState();
  put(state, "b2", "W");
  put(state, "d4", "b");
  put(state, "f6", "b");

  const first = applyCheckersMove(state, "white", { from: "b2", to: "e5" });
  assert.equal(first.continueCapture, true);
  assert.equal(state.board[fromSquare("d4").row][fromSquare("d4").col], "b");
  assert.deepEqual(legalCheckersMoves(state).map((move) => move.to), ["g7", "h8"]);

  applyCheckersMove(state, "white", { from: "e5", to: "g7" });
  assert.equal(state.board[fromSquare("d4").row][fromSquare("d4").col], null);
  assert.equal(state.board[fromSquare("f6").row][fromSquare("f6").col], null);
});
