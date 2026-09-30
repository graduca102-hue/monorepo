import { chessForClient } from "../games/chess.js";
import { checkersBoardForClient } from "../games/checkers.js";

const META = {
  chess: { icon: "♟", title: "Игра в шахматы", description: "Классическая партия на двоих" },
  checkers: { icon: "⛀", title: "Игра в шашки", description: "Русские шашки на двоих" },
};

const CHESS_GLYPHS = {
  wp: "♙", wn: "♘", wb: "♗", wr: "♖", wq: "♕", wk: "♔",
  bp: "♟", bn: "♞", bb: "♝", br: "♜", bq: "♛", bk: "♚",
};
const CHECKERS_GLYPHS = { w: "⛀", W: "⛁", b: "⛂", B: "⛃" };

const bold = (text) => ({ type: "bold", text });
const code = (text) => ({ type: "code", text });
const richButton = (text, action, style) => ({
  text,
  ...(style ? { style } : {}),
  ...action,
});
const buttonText = (button) => ({ type: "button", button });

export function displayName(user) {
  return [user?.firstName, user?.lastName].filter(Boolean).join(" ") || "Игрок";
}

function currentTurn(game) {
  if (game.type === "chess") return game.position.moves.length % 2 === 0 ? "white" : "black";
  return game.position.turn;
}

function boardPieces(game) {
  return game.type === "chess"
    ? chessForClient(game.position).pieces
    : checkersBoardForClient(game.position);
}

function tableCell(text, isHeader = false) {
  return {
    ...(text === undefined ? {} : { text }),
    align: "center",
    valign: "middle",
    ...(isHeader ? { is_header: true } : {}),
  };
}

function boardTable(game, options) {
  const orientation = options.orientation === "black" ? "black" : "white";
  const files = orientation === "white" ? [..."abcdefgh"] : [..."hgfedcba"];
  const ranks = orientation === "white" ? [8, 7, 6, 5, 4, 3, 2, 1] : [1, 2, 3, 4, 5, 6, 7, 8];
  const pieces = boardPieces(game);
  const targets = new Map((options.legalMoves || []).map((move) => [move.to, move]));
  const coordinateRow = () => [
    tableCell(undefined, true),
    ...files.map((file) => tableCell(bold(file), true)),
    tableCell(undefined, true),
  ];
  const cells = [coordinateRow()];

  for (const rank of ranks) {
    const row = [tableCell(bold(String(rank)), true)];
    for (const file of files) {
      const square = `${file}${rank}`;
      const piece = pieces[square];
      const glyph = game.type === "chess" ? CHESS_GLYPHS[piece] : CHECKERS_GLYPHS[piece];
      const target = targets.get(square);
      let style = "primary";
      if (options.selectedSquare === square || target) style = target?.capture ? "danger" : "success";
      const content = glyph || (target ? (target.capture ? "×" : "●") : "\u3164");
      const action = game.status === "playing"
        ? { callback_data: `s:${game.id}:${square}` }
        : { disabled: {} };
      row.push(tableCell(buttonText(richButton(content, action, style))));
    }
    row.push(tableCell(bold(String(rank)), true));
    cells.push(row);
  }
  cells.push(coordinateRow());

  return {
    type: "table",
    cells,
    is_bordered: true,
    is_compact: true,
  };
}

function statusBlock(game, options) {
  if (game.status === "finished") {
    const winner = game.result?.winner ? displayName(game.players[game.result.winner]) : null;
    return {
      type: "blockquote",
      blocks: [{
        type: "paragraph",
        text: winner
          ? ["🏆 ", bold(`${winner} победил`), ` — ${game.result.reason}`]
          : ["🤝 ", bold("Ничья"), ` — ${game.result?.reason || "Партия завершена"}`],
      }],
    };
  }

  if (options.promotion) {
    return {
      type: "blockquote",
      blocks: [{ type: "paragraph", text: ["♙ ", bold("Выберите фигуру для превращения")] }],
    };
  }

  const turn = currentTurn(game);
  const player = displayName(game.players[turn]);
  const forced = game.type === "checkers" && game.position.forcedPiece
    ? " Продолжите обязательное взятие."
    : "";
  return {
    type: "blockquote",
    blocks: [{
      type: "paragraph",
      text: [
        "↗ Ход ",
        bold(turn === "white" ? "белых" : "чёрных"),
        ` — ${player}. `,
        options.selectedSquare
          ? ["Выбрано ", code(options.selectedSquare), ", нажмите подсвеченную клетку."]
          : "Нажмите фигуру, затем клетку назначения.",
        forced,
      ],
    }],
  };
}

function historyBlock(game) {
  const history = game.type === "chess"
    ? game.position.moves.map((move) => move.san)
    : game.position.history.map((move) => move.notation);
  if (!history.length) return null;
  const tail = history.slice(-12);
  return {
    type: "details",
    summary: `${history.length}. ${history.at(-1)}`,
    blocks: [{ type: "paragraph", text: code(tail.map((move, index) => `${history.length - tail.length + index + 1}. ${move}`).join("  ")) }],
  };
}

function controls(game, options) {
  const blocks = [];
  if (options.promotion) {
    blocks.push({
      type: "buttons",
      align: "center",
      buttons: [
        richButton("♛ Ферзь", { callback_data: `p:${game.id}:q` }, "success"),
        richButton("♜ Ладья", { callback_data: `p:${game.id}:r` }),
        richButton("♝ Слон", { callback_data: `p:${game.id}:b` }),
        richButton("♞ Конь", { callback_data: `p:${game.id}:n` }),
      ],
    });
  }
  blocks.push({
    type: "buttons",
    align: "center",
    buttons: [
      richButton("↻ Перевернуть", { callback_data: `f:${game.id}` }, "primary"),
      richButton("↶ Отмена", { disabled: {} }),
    ],
  });
  blocks.push({
    type: "buttons",
    align: "center",
    buttons: game.status === "playing"
      ? [richButton("⊗ Завершить игру", { callback_data: `r:${game.id}` }, "danger")]
      : [richButton("Новая игра", { switch_inline_query_current_chat: "" }, "primary")],
  });
  return blocks;
}

export function waitingRichMessage(game) {
  const meta = META[game.type];
  return {
    blocks: [
      { type: "heading", size: 4, text: [meta.icon, " ", bold(meta.title)] },
      { type: "paragraph", text: [bold(displayName(game.host)), " ждёт соперника."] },
      {
        type: "buttons",
        align: "center",
        buttons: [richButton("Присоединиться", { callback_data: `j:${game.id}` }, "success")],
      },
    ],
  };
}

export function activeRichMessage(game, options = {}) {
  const meta = META[game.type];
  const history = historyBlock(game);
  return {
    blocks: [
      { type: "heading", size: 4, text: [meta.icon, " ", bold(meta.title)] },
      {
        type: "paragraph",
        text: ["⚪ ", bold(displayName(game.players.white)), "  ·  ⚫ ", bold(displayName(game.players.black))],
      },
      boardTable(game, options),
      statusBlock(game, options),
      ...(history ? [history] : []),
      ...controls(game, options),
    ],
    skip_entity_detection: true,
  };
}

export function inlineResult(game) {
  const meta = META[game.type];
  return {
    type: "article",
    id: `g:${game.id}`,
    title: `${meta.icon} ${meta.title}`,
    description: meta.description,
    input_message_content: { rich_message: waitingRichMessage(game) },
  };
}

export function gameMeta(type) {
  return META[type];
}
