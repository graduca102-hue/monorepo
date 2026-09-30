"""Thin asyncssh wrapper: password login, run commands, push files."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass

import asyncssh


class SSHError(RuntimeError):
    pass


@dataclass
class RunResult:
    cmd: str
    code: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.code == 0

    def check(self) -> "RunResult":
        if not self.ok:
            tail = (self.stderr or self.stdout).strip()[-800:]
            raise SSHError(f"`{self.cmd.splitlines()[0][:60]}` exited {self.code}: {tail}")
        return self


class Remote:
    def __init__(self, conn: asyncssh.SSHClientConnection):
        self._conn = conn

    async def run(self, cmd: str, *, timeout: int = 300, check: bool = False) -> RunResult:
        try:
            r = await asyncio.wait_for(self._conn.run(cmd), timeout=timeout)
        except asyncio.TimeoutError as e:
            raise SSHError(f"command timed out after {timeout}s: {cmd[:60]}") from e
        res = RunResult(cmd, r.exit_status or 0, str(r.stdout or ""), str(r.stderr or ""))
        return res.check() if check else res

    async def sudo_script(self, script: str, *, timeout: int = 600, check: bool = True) -> RunResult:
        """Run a multi-line bash script with ``set -e`` semantics."""
        wrapped = "set -euo pipefail\n" + script
        return await self.run(f"bash -s <<'VPNPANEL_EOF'\n{wrapped}\nVPNPANEL_EOF",
                              timeout=timeout, check=check)

    async def write_file(self, path: str, content: str, *, mode: str = "644") -> None:
        async with self._conn.start_sftp_client() as sftp:
            tmp = f"/tmp/.vpnpanel_{abs(hash(path)) % 10**8}"
            async with sftp.open(tmp, "w") as f:
                await f.write(content)
        await self.run(f"install -m {mode} -D {tmp} {path} && rm -f {tmp}", check=True)


@asynccontextmanager
async def connect(host: str, user: str, password: str, port: int = 22, *, timeout: int = 30):
    try:
        conn = await asyncio.wait_for(
            asyncssh.connect(
                host, port=port, username=user, password=password,
                known_hosts=None, client_keys=None,
            ),
            timeout=timeout,
        )
    except (OSError, asyncssh.Error, asyncio.TimeoutError) as e:
        raise SSHError(f"SSH connect to {user}@{host}:{port} failed: {e}") from e
    try:
        yield Remote(conn)
    finally:
        conn.close()
        await conn.wait_closed()
