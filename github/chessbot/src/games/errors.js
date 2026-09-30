export class GameError extends Error {
  constructor(message, status = 400, code = "GAME_ERROR") {
    super(message);
    this.name = "GameError";
    this.status = status;
    this.code = code;
  }
}
