import test from "node:test";
import assert from "node:assert/strict";
import { applyChessMove, chessForClient, createChessState } from "../src/games/chess.js";

test("chess turn and position are updated on a legal move", () => {
  const state = createChessState();
  const outcome = applyChessMove(state, "white", { from: "e2", to: "e4" });
  assert.equal(outcome.finished, false);
  assert.equal(chessForClient(state).turn, "black");
  assert.equal(chessForClient(state).pieces.e4, "wp");
});

test("server rejects moving for the opponent", () => {
  const state = createChessState();
  assert.throws(
    () => applyChessMove(state, "black", { from: "e7", to: "e5" }),
    { code: "NOT_YOUR_TURN" },
  );
});

test("fool's mate is detected as checkmate", () => {
  const state = createChessState();
  applyChessMove(state, "white", { from: "f2", to: "f3" });
  applyChessMove(state, "black", { from: "e7", to: "e5" });
  applyChessMove(state, "white", { from: "g2", to: "g4" });
  const outcome = applyChessMove(state, "black", { from: "d8", to: "h4" });
  assert.equal(outcome.finished, true);
  assert.deepEqual(outcome.result, { winner: "black", reason: "Мат" });
});
