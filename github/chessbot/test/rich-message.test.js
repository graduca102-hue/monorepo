import test from "node:test";
import assert from "node:assert/strict";
import {
  activeRichMessage,
  inlineResult,
  waitingRichMessage,
} from "../src/telegram/cards.js";
import { createChessState } from "../src/games/chess.js";

function game(status = "playing") {
  return {
    id: "RichGame123",
    type: "chess",
    status,
    host: { id: "1", firstName: "Alice", lastName: "", username: "", photoUrl: "" },
    players: {
      white: { id: "1", firstName: "Alice", lastName: "", username: "", photoUrl: "" },
      black: { id: "2", firstName: "Bob", lastName: "", username: "", photoUrl: "" },
    },
    position: createChessState(),
    result: null,
  };
}

test("inline invitation uses InputRichMessageContent rather than Mini App markup", () => {
  const invitation = inlineResult(game("waiting"));
  assert.ok(invitation.input_message_content.rich_message);
  assert.equal(invitation.reply_markup, undefined);
  const join = waitingRichMessage(game("waiting")).blocks.find((block) => block.type === "buttons");
  assert.equal(join.buttons[0].callback_data, "j:RichGame123");
});

test("active Rich Message contains a compact 10x10 board with coordinates on every side", () => {
  const message = activeRichMessage(game(), {
    orientation: "white",
    selectedSquare: "e2",
    legalMoves: [{ from: "e2", to: "e4", capture: false }],
  });
  const table = message.blocks.find((block) => block.type === "table");
  assert.equal(table.is_compact, true);
  assert.equal(table.cells.length, 10);
  assert.ok(table.cells.every((row) => row.length === 10));
  assert.deepEqual(
    table.cells[0].slice(1, 9).map((cell) => cell.text.text),
    [..."abcdefgh"],
  );
  assert.deepEqual(
    table.cells[9].slice(1, 9).map((cell) => cell.text.text),
    [..."abcdefgh"],
  );
  const buttons = table.cells.flat()
    .map((cell) => cell.text)
    .filter((text) => text?.type === "button");
  assert.equal(buttons.length, 64);
  assert.equal(buttons.find((entry) => entry.button.callback_data.endsWith(":e2")).button.style, "success");
  const target = buttons.find((entry) => entry.button.callback_data.endsWith(":e4")).button;
  assert.equal(target.style, "success");
  assert.equal(target.text, "●");
  const empty = buttons.find((entry) => entry.button.callback_data.endsWith(":b6")).button;
  assert.equal(empty.style, "primary");
  assert.equal(empty.text, "\u3164");
});
