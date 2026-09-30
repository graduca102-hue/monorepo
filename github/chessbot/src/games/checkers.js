import { GameError } from "./errors.js";

const BOARD_SIZE = 8;
const DIAGONALS = [
  [-1, -1],
  [-1, 1],
  [1, -1],
  [1, 1],
];

const inside = (row, col) => row >= 0 && row < BOARD_SIZE && col >= 0 && col < BOARD_SIZE;
const owner = (piece) => (piece?.toLowerCase() === "w" ? "white" : piece ? "black" : null);
const enemyOf = (color) => (color === "white" ? "black" : "white");
const isKing = (piece) => piece === "W" || piece === "B";

export function toSquare(row, col) {
  return `${String.fromCharCode(97 + col)}${8 - row}`;
}

export function fromSquare(square) {
  if (!/^[a-h][1-8]$/.test(String(square))) return null;
  return { row: 8 - Number(square[1]), col: square.charCodeAt(0) - 97 };
}

export function createCheckersState() {
  const board = Array.from({ length: BOARD_SIZE }, () => Array(BOARD_SIZE).fill(null));
  for (let row = 0; row < 3; row += 1) {
    for (let col = 0; col < BOARD_SIZE; col += 1) {
      if ((row + col) % 2 === 1) board[row][col] = "b";
    }
  }
  for (let row = 5; row < BOARD_SIZE; row += 1) {
    for (let col = 0; col < BOARD_SIZE; col += 1) {
      if ((row + col) % 2 === 1) board[row][col] = "w";
    }
  }

  return {
    board,
    turn: "white",
    forcedPiece: null,
    capturedInTurn: [],
    lastMove: null,
    history: [],
  };
}

function manCaptures(board, row, col, color, capturedInTurn = []) {
  const moves = [];
  const alreadyCaptured = new Set(capturedInTurn);
  for (const [dr, dc] of DIAGONALS) {
    const capturedRow = row + dr;
    const capturedCol = col + dc;
    const targetRow = row + dr * 2;
    const targetCol = col + dc * 2;
    const capturedSquare = toSquare(capturedRow, capturedCol);
    if (
      inside(targetRow, targetCol) &&
      owner(board[capturedRow]?.[capturedCol]) === enemyOf(color) &&
      !alreadyCaptured.has(capturedSquare) &&
      !board[targetRow][targetCol]
    ) {
      moves.push({
        from: toSquare(row, col),
        to: toSquare(targetRow, targetCol),
        capture: capturedSquare,
      });
    }
  }
  return moves;
}

function kingCaptures(board, row, col, color, capturedInTurn = []) {
  const moves = [];
  const alreadyCaptured = new Set(capturedInTurn);
  for (const [dr, dc] of DIAGONALS) {
    let scanRow = row + dr;
    let scanCol = col + dc;
    let captured = null;

    while (inside(scanRow, scanCol)) {
      const piece = board[scanRow][scanCol];
      if (!piece && !captured) {
        scanRow += dr;
        scanCol += dc;
        continue;
      }
      if (piece && !captured) {
        const occupiedSquare = toSquare(scanRow, scanCol);
        if (owner(piece) === color || alreadyCaptured.has(occupiedSquare)) break;
        captured = occupiedSquare;
        scanRow += dr;
        scanCol += dc;
        continue;
      }
      if (piece) break;

      moves.push({ from: toSquare(row, col), to: toSquare(scanRow, scanCol), capture: captured });
      scanRow += dr;
      scanCol += dc;
    }
  }
  return moves;
}

function capturesForPiece(board, row, col, color, capturedInTurn = []) {
  const piece = board[row][col];
  if (!piece || owner(piece) !== color) return [];
  return isKing(piece)
    ? kingCaptures(board, row, col, color, capturedInTurn)
    : manCaptures(board, row, col, color, capturedInTurn);
}

function quietMovesForPiece(board, row, col, color) {
  const piece = board[row][col];
  const moves = [];
  if (isKing(piece)) {
    for (const [dr, dc] of DIAGONALS) {
      let targetRow = row + dr;
      let targetCol = col + dc;
      while (inside(targetRow, targetCol) && !board[targetRow][targetCol]) {
        moves.push({ from: toSquare(row, col), to: toSquare(targetRow, targetCol), capture: null });
        targetRow += dr;
        targetCol += dc;
      }
    }
    return moves;
  }

  const forward = color === "white" ? -1 : 1;
  for (const dc of [-1, 1]) {
    const targetRow = row + forward;
    const targetCol = col + dc;
    if (inside(targetRow, targetCol) && !board[targetRow][targetCol]) {
      moves.push({ from: toSquare(row, col), to: toSquare(targetRow, targetCol), capture: null });
    }
  }
  return moves;
}

export function legalCheckersMoves(state, color = state.turn) {
  if (state.forcedPiece && color === state.turn) {
    const forced = fromSquare(state.forcedPiece);
    return forced
      ? capturesForPiece(state.board, forced.row, forced.col, color, state.capturedInTurn || [])
      : [];
  }

  const captures = [];
  const quiet = [];
  for (let row = 0; row < BOARD_SIZE; row += 1) {
    for (let col = 0; col < BOARD_SIZE; col += 1) {
      if (owner(state.board[row][col]) !== color) continue;
      captures.push(...capturesForPiece(state.board, row, col, color, state.capturedInTurn || []));
      quiet.push(...quietMovesForPiece(state.board, row, col, color));
    }
  }
  return captures.length ? captures : quiet;
}

function promote(piece, row) {
  if (piece === "w" && row === 0) return "W";
  if (piece === "b" && row === 7) return "B";
  return piece;
}

export function applyCheckersMove(state, color, requestedMove) {
  if (state.turn !== color) throw new GameError("Сейчас ход соперника", 409, "NOT_YOUR_TURN");

  const legal = legalCheckersMoves(state, color);
  const move = legal.find(
    (candidate) => candidate.from === requestedMove.from && candidate.to === requestedMove.to,
  );
  if (!move) throw new GameError("Недопустимый ход", 422, "ILLEGAL_MOVE");

  const source = fromSquare(move.from);
  const target = fromSquare(move.to);
  let piece = state.board[source.row][source.col];
  state.board[source.row][source.col] = null;
  if (move.capture) {
    state.capturedInTurn ||= [];
    state.capturedInTurn.push(move.capture);
  }
  piece = promote(piece, target.row);
  state.board[target.row][target.col] = piece;
  state.lastMove = { ...move };
  state.history.push({
    notation: `${move.from}${move.capture ? "×" : "–"}${move.to}`,
    color,
  });

  if (move.capture) {
    const continuation = capturesForPiece(
      state.board,
      target.row,
      target.col,
      color,
      state.capturedInTurn,
    );
    if (continuation.length) {
      state.forcedPiece = move.to;
      return { finished: false, continueCapture: true };
    }
  }

  for (const capturedSquare of state.capturedInTurn || []) {
    const captured = fromSquare(capturedSquare);
    state.board[captured.row][captured.col] = null;
  }
  state.capturedInTurn = [];
  state.forcedPiece = null;
  state.turn = enemyOf(color);
  const opponentHasPieces = state.board.some((row) => row.some((pieceOnBoard) => owner(pieceOnBoard) === state.turn));
  const opponentHasMoves = opponentHasPieces && legalCheckersMoves(state, state.turn).length > 0;
  if (!opponentHasMoves) {
    return {
      finished: true,
      result: { winner: color, reason: opponentHasPieces ? "Нет доступных ходов" : "Все шашки взяты" },
    };
  }

  return { finished: false, continueCapture: false };
}

export function checkersBoardForClient(state) {
  const pieces = {};
  for (let row = 0; row < BOARD_SIZE; row += 1) {
    for (let col = 0; col < BOARD_SIZE; col += 1) {
      if (state.board[row][col]) pieces[toSquare(row, col)] = state.board[row][col];
    }
  }
  return pieces;
}
