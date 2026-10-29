"""cctl ssh: SSH helpers for a launched instance (port of the legacy cctl.sh).

    cctl ssh [user@]HOST [-u USER] [-p PORT] [-i KEY] [-o OPT]... [-R] <action> [...]

The remote-side shell scripts are kept verbatim from the Bash version as string
constants; only the local orchestration lives here. Standard library only.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from .errors import CctlError

DEFAULT_LOGIN_USER = "cc"  # default account on Chameleon images
DEFAULT_PORT = "22"

_USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]*[$]?$")
_PUBKEY_PREFIXES = (
    "ssh-rsa ",
    "ssh-ed25519 ",
    "sk-ssh-ed25519@openssh.com ",
    "sk-ecdsa-sha2-nistp256@openssh.com ",
)


# ---------------------------------------------------------------------------
# Remote scripts (verbatim from legacy/cctl.sh heredocs)
# ---------------------------------------------------------------------------

# Wrapper executed on the remote host by send_remote_script_tty. It reads the
# sudo password (first stdin line) and the base64 script (rest of stdin), then
# validates sudo and runs the script as root.
REMOTE_SUDO_WRAPPER = r"""set -euo pipefail
IFS= read -r sudo_password
script_b64="$(cat)"
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
printf '%s\n' "$script_b64" | base64 -d > "$tmp"
echo "remote: validating sudo access"
if sudo -n true 2>/dev/null; then
  echo "remote: sudo validated without password"
  sudo bash "$tmp"
else
  if [[ -z "$sudo_password" ]]; then
    echo "remote error: sudo requires a password" >&2
    exit 1
  fi
  printf '%s\n' "$sudo_password" | sudo -S -p '' true || {
    echo "remote error: sudo password was rejected" >&2
    exit 1
  }
  echo "remote: sudo validated"
  printf '%s\n' "$sudo_password" | sudo -S -p '' bash "$tmp"
fi
"""

REMOTE_ADDUSER = r"""set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
  echo "remote error: this script must run as root" >&2
  exit 1
fi

if getent passwd "$NEW_USER" >/dev/null; then
  echo "remote: user $NEW_USER already exists"
else
  useradd -m -s /bin/bash "$NEW_USER"
  echo "remote: created user $NEW_USER"
fi

if getent group sudo >/dev/null; then
  usermod -aG sudo "$NEW_USER"
  echo "remote: added $NEW_USER to sudo"
elif getent group wheel >/dev/null; then
  usermod -aG wheel "$NEW_USER"
  echo "remote: added $NEW_USER to wheel"
else
  echo "remote error: neither sudo nor wheel group exists" >&2
  exit 1
fi

home_dir="$(getent passwd "$NEW_USER" | cut -d: -f6)"
install -d -m 700 -o "$NEW_USER" -g "$NEW_USER" "$home_dir/.ssh"
authorized_keys="$home_dir/.ssh/authorized_keys"
touch "$authorized_keys"
chown "$NEW_USER:$NEW_USER" "$authorized_keys"
chmod 600 "$authorized_keys"

if grep -qxF "$PUBLIC_KEY" "$authorized_keys"; then
  echo "remote: public key already present"
else
  printf '%s\n' "$PUBLIC_KEY" >> "$authorized_keys"
  echo "remote: public key added"
fi

if [[ -n "$PASSWORD" ]]; then
  printf '%s:%s\n' "$NEW_USER" "$PASSWORD" | chpasswd
  echo "remote: password updated"
  if [[ "$FORCE_PASSWORD_CHANGE" == "1" ]]; then
    chage -d 0 "$NEW_USER"
    echo "remote: password change required at next login"
  fi
else
  echo "remote: password unchanged"
fi

echo "remote: done"
"""

REMOTE_ADDKEY = r"""set -euo pipefail
getent passwd "$TARGET_USER" >/dev/null || { echo "remote error: user does not exist: $TARGET_USER" >&2; exit 1; }
home_dir="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
install -d -m 700 -o "$TARGET_USER" -g "$TARGET_USER" "$home_dir/.ssh"
authorized_keys="$home_dir/.ssh/authorized_keys"
touch "$authorized_keys"
chown "$TARGET_USER:$TARGET_USER" "$authorized_keys"
chmod 600 "$authorized_keys"
if grep -qxF "$PUBLIC_KEY" "$authorized_keys"; then
  echo "remote: public key already present"
else
  printf '%s\n' "$PUBLIC_KEY" >> "$authorized_keys"
  echo "remote: public key added"
fi
"""

REMOTE_FISH = r"""set -euo pipefail

getent passwd "$TARGET_USER" >/dev/null || {
  echo "remote error: user does not exist: $TARGET_USER" >&2
  exit 1
}

if command -v fish >/dev/null 2>&1; then
  echo "remote: fish already installed"
else
  echo "remote: installing fish"
  if command -v apt-get >/dev/null 2>&1; then
    echo "remote: apt-get update"
    apt-get -o DPkg::Lock::Timeout=60 update
    echo "remote: apt-get install fish"
    DEBIAN_FRONTEND=noninteractive apt-get -o DPkg::Lock::Timeout=60 install -y fish
  elif command -v dnf >/dev/null 2>&1; then
    echo "remote: dnf install fish"
    dnf install -y fish
  elif command -v yum >/dev/null 2>&1; then
    echo "remote: yum install fish"
    yum install -y fish
  elif command -v zypper >/dev/null 2>&1; then
    echo "remote: zypper install fish"
    zypper --non-interactive install fish
  elif command -v pacman >/dev/null 2>&1; then
    echo "remote: pacman install fish"
    pacman -Sy --noconfirm fish
  else
    echo "remote error: no supported package manager found for installing fish" >&2
    exit 1
  fi
fi

fish_path="$(command -v fish)"
if ! grep -qxF "$fish_path" /etc/shells 2>/dev/null; then
  echo "$fish_path" >> /etc/shells
  echo "remote: added $fish_path to /etc/shells"
fi

current_shell="$(getent passwd "$TARGET_USER" | cut -d: -f7)"
if [[ "$current_shell" == "$fish_path" ]]; then
  echo "remote: $TARGET_USER already uses $fish_path"
else
  chsh -s "$fish_path" "$TARGET_USER"
  echo "remote: set $TARGET_USER shell to $fish_path"
fi

echo "remote: done"
"""

REMOTE_NOPASSWD = r"""set -euo pipefail

getent passwd "$TARGET_USER" >/dev/null || {
  echo "remote error: user does not exist: $TARGET_USER" >&2
  exit 1
}

command -v visudo >/dev/null 2>&1 || {
  echo "remote error: visudo is required" >&2
  exit 1
}

install -d -m 755 /etc/sudoers.d
sudoers_file="/etc/sudoers.d/${TARGET_USER}-nopasswd"
tmp_file="$(mktemp)"
trap 'rm -f "$tmp_file"' EXIT

printf '%s ALL=(ALL) NOPASSWD:ALL\n' "$TARGET_USER" > "$tmp_file"
chmod 440 "$tmp_file"

if visudo -cf "$tmp_file" >/dev/null; then
  install -m 440 "$tmp_file" "$sudoers_file"
else
  echo "remote error: generated sudoers file failed visudo validation" >&2
  visudo -cf "$tmp_file" >&2 || true
  exit 1
fi

visudo -cf /etc/sudoers >/dev/null
echo "remote: installed $sudoers_file"
echo "remote: $TARGET_USER can now run sudo without a password"
"""

REMOTE_STATUS_COMMAND = "hostname; uptime; uname -a; df -h /; free -h 2>/dev/null || true"

REMOTE_GPU = r"""set -u

section() {
  printf '\n## %s\n' "$1"
}

have() {
  command -v "$1" >/dev/null 2>&1
}

section "Host"
printf 'hostname: %s\n' "$(hostname 2>/dev/null || printf unknown)"
printf 'kernel:   %s\n' "$(uname -r 2>/dev/null || printf unknown)"
if have lsb_release; then
  lsb_release -ds 2>/dev/null | sed 's/^/os:       /'
elif [[ -r /etc/os-release ]]; then
  . /etc/os-release
  printf 'os:       %s\n' "${PRETTY_NAME:-unknown}"
fi

section "GPU Hardware"
if have lspci; then
  gpu_lines="$(lspci -nn 2>/dev/null | grep -Ei '^[[:xdigit:]:.]+ (VGA compatible controller|3D controller|Display controller):|NVIDIA Corporation' || true)"
  if [[ -n "$gpu_lines" ]]; then
    printf '%s\n' "$gpu_lines"
  else
    printf 'no GPU-like PCI devices found by lspci\n'
  fi
else
  printf 'lspci is not installed\n'
fi

section "NVIDIA Driver"
if have nvidia-smi; then
  query_output="$(nvidia-smi --query-gpu=index,name,uuid,driver_version,memory.total,memory.used,temperature.gpu,power.draw --format=csv,noheader,nounits 2>&1)"
  query_status="$?"
  if [[ "$query_status" -eq 0 ]]; then
    printf '%s\n' "$query_output" \
      | awk -F', ' 'BEGIN { print "index | name | uuid | driver | mem MiB | used MiB | temp C | power W" } { print $1 " | " $2 " | " $3 " | " $4 " | " $5 " | " $6 " | " $7 " | " $8 }'
  else
    printf 'nvidia-smi query failed:\n%s\n' "$query_output"
  fi
else
  printf 'nvidia-smi not found\n'
fi

section "NVIDIA Kernel Modules"
if have lsmod; then
  lsmod | grep -E '^nvidia' || printf 'no loaded nvidia kernel modules found\n'
else
  printf 'lsmod is not installed\n'
fi

if [[ -r /proc/driver/nvidia/version ]]; then
  printf '\n/proc/driver/nvidia/version:\n'
  cat /proc/driver/nvidia/version
fi

section "CUDA Toolkit"
if have nvcc; then
  nvcc --version 2>&1
else
  printf 'nvcc not found in PATH\n'
fi

for cuda_dir in /usr/local/cuda /usr/local/cuda-*; do
  if [[ -e "$cuda_dir" ]]; then
    printf 'cuda path: %s\n' "$cuda_dir"
    if [[ -r "$cuda_dir/version.json" ]]; then
      grep -E '"cuda"|"version"' "$cuda_dir/version.json" 2>/dev/null | head -20 || true
    elif [[ -r "$cuda_dir/version.txt" ]]; then
      cat "$cuda_dir/version.txt"
    fi
  fi
done

section "CUDA Libraries"
if have ldconfig; then
  ldconfig -p 2>/dev/null | grep -E 'libcuda|libcudart|libcublas|libcudnn|libnccl' || printf 'no common CUDA libraries found by ldconfig\n'
else
  printf 'ldconfig is not available\n'
fi

section "Python GPU Packages"
if have python3; then
  python3 - <<'PY' 2>&1
import importlib.util

packages = ["torch", "tensorflow", "jax", "cupy"]
for name in packages:
    spec = importlib.util.find_spec(name)
    if spec is None:
        print(f"{name}: not installed")
        continue
    try:
        module = __import__(name)
        version = getattr(module, "__version__", "unknown")
        print(f"{name}: {version}")
        if name == "torch":
            print(f"  cuda available: {module.cuda.is_available()}")
            print(f"  cuda version:    {module.version.cuda}")
            print(f"  device count:    {module.cuda.device_count()}")
            for index in range(module.cuda.device_count()):
                print(f"  device {index}:      {module.cuda.get_device_name(index)}")
        elif name == "tensorflow":
            print(f"  gpu devices:     {module.config.list_physical_devices('GPU')}")
        elif name == "jax":
            print(f"  devices:         {module.devices()}")
        elif name == "cupy":
            print(f"  cuda runtime:    {module.cuda.runtime.runtimeGetVersion()}")
            print(f"  device count:    {module.cuda.runtime.getDeviceCount()}")
    except Exception as exc:
        print(f"{name}: installed but check failed: {exc}")
PY
else
  printf 'python3 is not installed\n'
fi

section "Container GPU Runtime"
if have docker; then
  docker info 2>/dev/null | grep -Ei 'runtimes|nvidia|default runtime' || printf 'docker found, but no NVIDIA runtime lines found or docker is inaccessible\n'
else
  printf 'docker not found\n'
fi

if have nvidia-container-cli; then
  nvidia-container-cli --version 2>&1
else
  printf 'nvidia-container-cli not found\n'
fi

section "Summary"
if have nvidia-smi && nvidia-smi -L >/dev/null 2>&1; then
  gpu_count="$(nvidia-smi -L 2>/dev/null | wc -l | tr -d ' ')"
  printf 'NVIDIA GPUs detected: %s\n' "$gpu_count"
else
  printf 'NVIDIA GPUs detected: no, or driver is not working\n'
fi

if have nvcc; then
  printf 'CUDA toolkit: yes\n'
else
  printf 'CUDA toolkit: not found in PATH\n'
fi
"""


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

class SshConn:
    """Local view of one target: host, login user, port, key, extra -o options."""

    def __init__(self, args: argparse.Namespace):
        target = (args.target or "").strip()
        if not target:
            raise CctlError("host cannot be empty")
        login_user = None
        host = target
        if "@" in target:
            login_user, host = target.rsplit("@", 1)
            if not login_user:
                raise CctlError("login user in host cannot be empty")
            if not host:
                raise CctlError("host cannot be empty")
        if getattr(args, "login_user", None):
            login_user = args.login_user
        if not login_user:
            login_user = DEFAULT_LOGIN_USER or os.environ.get("USER") or getpass.getuser()
        if not login_user:
            raise CctlError("missing --user and local USER is not set")

        port = str(getattr(args, "port", None) or DEFAULT_PORT)
        if not port.isdigit() or not 1 <= int(port) <= 65535:
            raise CctlError(f"invalid SSH port: {port}")

        self.host = host
        self.login_user = login_user
        self.port = port
        self.identity_file = getattr(args, "identity_file", None) or ""
        self.ssh_options: list[str] = list(getattr(args, "ssh_option", None) or [])

    @property
    def target(self) -> str:
        return f"{self.login_user}@{self.host}"

    def base_args(self) -> list[str]:
        """Same as ssh_base_args in cctl.sh."""
        args = ["-p", self.port, "-o", "BatchMode=no", "-o", "StrictHostKeyChecking=accept-new"]
        if self.identity_file:
            args += ["-i", str(Path(self.identity_file).expanduser())]
        for opt in self.ssh_options:
            args += ["-o", opt]
        return args

    def command(self, *pre: str, remote: list[str] | None = None) -> list[str]:
        return [_ssh_bin(), *pre, *self.base_args(), self.target, *(remote or [])]


def _ssh_bin() -> str:
    path = shutil.which("ssh")
    if not path:
        raise CctlError("ssh is not installed or not on PATH")
    return path


def _run(cmd: list[str], **kwargs) -> int:
    try:
        return subprocess.run(cmd, **kwargs).returncode
    except FileNotFoundError as exc:
        raise CctlError(f"cannot run {cmd[0]}: {exc}") from exc
    except OSError as exc:
        raise CctlError(f"failed to start {cmd[0]}: {exc}") from exc


def _check_rc(rc: int, what: str) -> None:
    if rc != 0:
        raise CctlError(f"{what} failed (ssh exit status {rc})",
                        hint="see the ssh / remote messages above", exit_code=rc if 0 < rc < 256 else 1)


def replace_known_host(ctx, conn: SshConn) -> None:
    if not shutil.which("ssh-keygen"):
        raise CctlError("ssh-keygen is required for --replace-host-key")
    target = conn.host if conn.port == "22" else f"[{conn.host}]:{conn.port}"
    if not ctx.confirm(f"Remove old SSH host key entry for {target}?"):
        raise CctlError("host key replacement cancelled")
    ctx.out.info(f"removing old SSH host key entry for {target}")
    _run(["ssh-keygen", "-R", target], stdout=subprocess.DEVNULL)


def run_ssh(conn: SshConn, remote_command: list[str]) -> int:
    return _run(conn.command(remote=remote_command))


def run_ssh_tty(conn: SshConn, remote_command: list[str]) -> int:
    return _run(conn.command("-tt", remote=remote_command))


def _read_tty_secret(prompt: str) -> str:
    """Read a secret from the controlling terminal (like `read -s < /dev/tty`)."""
    try:
        return getpass.getpass(prompt)
    except EOFError:
        return ""


def send_remote_script_tty(ctx, conn: SshConn, script: str, env: dict[str, str]) -> None:
    """Run ``script`` as root on the remote host.

    Prompts for the login user's sudo password locally and sends it, followed
    by the base64-encoded script, over ssh's stdin to REMOTE_SUDO_WRAPPER,
    which feeds it to ``sudo -S``. The password is never logged or put on a
    command line.
    """
    sudo_password = _read_tty_secret(
        f"Sudo password for {conn.target} (leave empty to try passwordless sudo): ")

    prelude = "".join(f"{name}={shlex.quote(value)}\n" for name, value in env.items())
    script_b64 = base64.b64encode((prelude + script).encode()).decode()
    payload = f"{sudo_password}\n{script_b64}\n".encode()

    remote = ["bash -c " + shlex.quote(REMOTE_SUDO_WRAPPER)]
    rc = _run(conn.command(remote=remote), input=payload)
    _check_rc(rc, "remote script")


def send_remote_user_script(conn: SshConn, script: str) -> int:
    """Run ``script`` as the login user (``bash -s`` with the script on stdin)."""
    return _run(conn.command(remote=["bash -s"]), input=script.encode())


# ---------------------------------------------------------------------------
# Input validation helpers
# ---------------------------------------------------------------------------

def validate_username(user: str) -> str:
    if not _USERNAME_RE.match(user or ""):
        raise CctlError(f"invalid Linux username: {user}")
    return user


def read_public_key(value: str) -> str:
    """Accept an OpenSSH public key string or a path to one."""
    candidate = Path(value).expanduser()
    if candidate.is_file():
        try:
            value = candidate.read_text().replace("\r", "").replace("\n", "")
        except OSError as exc:
            raise CctlError(f"cannot read public key {candidate}: {exc}") from exc
    value = value or ""
    if not value:
        raise CctlError("public key is empty")
    if value.startswith(_PUBKEY_PREFIXES) or (value.startswith("ecdsa-sha2-") and " " in value):
        return value
    raise CctlError("public key must be a valid OpenSSH public key string or a path to one",
                    hint="e.g. --pubkey ~/.ssh/id_ed25519.pub")


def prompt_password() -> str:
    first = _read_tty_secret("New password: ")
    second = _read_tty_secret("Confirm password: ")
    if first != second:
        raise CctlError("passwords do not match")
    if not first:
        raise CctlError("password cannot be empty")
    return first


def validate_tunnel_port(port: str) -> str:
    if not re.fullmatch(r"[0-9]{1,5}", port):
        raise CctlError(f"invalid tunnel port: {port}")
    if not 1 <= int(port) <= 65535:
        raise CctlError(f"tunnel port must be between 1 and 65535: {port}")
    return port


def normalize_forward(spec: str) -> str:
    """PORT -> PORT:localhost:PORT; L:R -> L:localhost:R; else OpenSSH -L spec."""
    if re.fullmatch(r"[0-9]+", spec):
        validate_tunnel_port(spec)
        return f"{spec}:localhost:{spec}"
    m = re.fullmatch(r"([0-9]+):([0-9]+)", spec)
    if m:
        local_port, remote_port = m.group(1), m.group(2)
        validate_tunnel_port(local_port)
        validate_tunnel_port(remote_port)
        return f"{local_port}:localhost:{remote_port}"
    if spec.count(":") < 2:
        raise CctlError(f"invalid forward '{spec}'; expected PORT, LOCAL_PORT:REMOTE_PORT, "
                        "or an OpenSSH -L specification")
    return spec


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _connect(ctx, args) -> SshConn:
    conn = SshConn(args)
    if getattr(args, "replace_host_key", False):
        replace_known_host(ctx, conn)
    return conn


def _target_user(conn: SshConn, args) -> str:
    return validate_username(getattr(args, "target_user", None) or conn.login_user)


def cmd_adduser(ctx, args):
    conn = _connect(ctx, args)
    new_user = validate_username(args.target_user)
    pubkey = read_public_key(args.pubkey)

    if args.no_password:
        password = ""
    elif args.password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
        if not password:
            raise CctlError("password read from stdin is empty")
    elif args.password is not None:
        password = args.password
        if not password:
            raise CctlError("--password cannot be empty")
    else:
        password = prompt_password()

    key_parts = pubkey.split(" ")
    print(f"Target: {conn.target}")
    print(f"User:   {new_user}")
    print(f"Key:    {key_parts[0]} {key_parts[-1]}")
    if not ctx.confirm(f"Provision this user on {conn.host}?"):
        raise CctlError("cancelled")

    ctx.out.info(f"connecting to {conn.host} as {conn.login_user}")
    send_remote_script_tty(ctx, conn, REMOTE_ADDUSER, {
        "NEW_USER": new_user,
        "PUBLIC_KEY": pubkey,
        "PASSWORD": password,
        "FORCE_PASSWORD_CHANGE": "1" if args.force_password_change else "0",
    })
    ctx.out.info(f"try: ssh {new_user}@{conn.host}")


def cmd_addkey(ctx, args):
    conn = _connect(ctx, args)
    target_user = _target_user(conn, args)
    pubkey = read_public_key(args.pubkey)
    send_remote_script_tty(ctx, conn, REMOTE_ADDKEY, {
        "TARGET_USER": target_user,
        "PUBLIC_KEY": pubkey,
    })


def cmd_fish(ctx, args):
    conn = _connect(ctx, args)
    target_user = _target_user(conn, args)
    send_remote_script_tty(ctx, conn, REMOTE_FISH, {"TARGET_USER": target_user})


def cmd_nopasswd(ctx, args):
    conn = _connect(ctx, args)
    target_user = _target_user(conn, args)
    send_remote_script_tty(ctx, conn, REMOTE_NOPASSWD, {"TARGET_USER": target_user})


def cmd_sudo(ctx, args):
    conn = _connect(ctx, args)
    target_user = _target_user(conn, args)
    if target_user == conn.login_user:
        remote = "sudo -l && id"
    else:
        remote = f"sudo -l -U {shlex.quote(target_user)} && id {shlex.quote(target_user)}"
    return run_ssh_tty(conn, [remote])


def cmd_status(ctx, args):
    conn = _connect(ctx, args)
    return run_ssh(conn, [REMOTE_STATUS_COMMAND])


def cmd_gpu(ctx, args):
    conn = _connect(ctx, args)
    return send_remote_user_script(conn, REMOTE_GPU)


def cmd_run(ctx, args):
    conn = _connect(ctx, args)
    command = list(args.command or [])
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise CctlError("run requires a command", hint="cctl ssh HOST run -- whoami")
    return run_ssh(conn, command)


def cmd_tunnel(ctx, args):
    conn = _connect(ctx, args)
    forwards = [normalize_forward(spec) for spec in (args.forward or [])]
    if not forwards:
        raise CctlError("tunnel requires at least one -L or --forward")

    cmd = [_ssh_bin(), "-N", "-T", "-o", "ExitOnForwardFailure=yes", *conn.base_args()]
    ctx.out.info(f"opening SSH tunnel to {conn.target}")
    for spec in forwards:
        ctx.out.info(f"forward: {spec}")
        cmd += ["-L", spec]
    cmd.append(conn.target)
    ctx.out.info("press Ctrl-C to close the tunnel")

    sys.stdout.flush()
    sys.stderr.flush()
    try:
        os.execv(cmd[0], cmd)  # foreground; ssh replaces this process
    except OSError as exc:
        raise CctlError(f"failed to exec ssh: {exc}") from exc


# ---------------------------------------------------------------------------
# argparse registration
# ---------------------------------------------------------------------------

_TUNNEL_EPILOG = """\
forward formats:
  PORT                                   same-port shorthand for PORT:localhost:PORT
  LOCAL_PORT:REMOTE_PORT                 shorthand for LOCAL_PORT:localhost:REMOTE_PORT
  LOCAL_PORT:REMOTE_HOST:REMOTE_PORT     standard OpenSSH local-forward formats
  BIND_ADDRESS:LOCAL_PORT:REMOTE_HOST:REMOTE_PORT

examples:
  cctl ssh user@host tunnel -L 8888
  cctl ssh user@host tunnel -L 8888:8888
  cctl ssh user@host tunnel -L 8888:localhost:8888 -L 6006:localhost:6006
  cctl ssh user@host tunnel -L 127.0.0.1:15432:db.internal:5432

The tunnel runs in the foreground. Press Ctrl-C to close all forwards.
"""

_GPU_DESCRIPTION = """\
Check GPUs, NVIDIA driver, CUDA, and runtimes on the instance.

checks:
  - GPU-like PCI devices
  - NVIDIA driver and nvidia-smi
  - NVIDIA kernel modules
  - CUDA toolkit and common CUDA libraries
  - Python packages with GPU support checks
  - Docker/NVIDIA container runtime presence
"""

_SSH_EPILOG = """\
examples:
  cctl ssh cc@192.5.86.156 adduser --user llamacpp --pubkey ~/.ssh/id_ed25519.pub
  cctl ssh llamacpp@192.5.86.156 fish
  cctl ssh llamacpp@192.5.86.156 tunnel -L 8888 -L 6006
  cctl ssh 192.5.86.156 run -- whoami
"""


def _user_option(parser: argparse.ArgumentParser, help_text: str, required: bool = False) -> None:
    parser.add_argument("-u", "--user", dest="target_user", metavar="USER", required=required, help=help_text)


def register(sub, parents):
    p = sub.add_parser(
        "ssh", parents=parents,
        help="SSH helpers: provision users, keys, fish, sudo, status, gpu, tunnel, run",
        description="Operate on a launched instance over SSH. HOST may be USER@HOST; "
                    f"the login user defaults to '{DEFAULT_LOGIN_USER}' (Chameleon images).",
        epilog=_SSH_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("target", metavar="[USER@]HOST", help="instance address, optionally with the SSH login user")
    p.add_argument("-u", "--user", dest="login_user", metavar="USER",
                   help=f"SSH login user, overrides USER@HOST (default: {DEFAULT_LOGIN_USER})")
    p.add_argument("-p", "--port", metavar="PORT", default=DEFAULT_PORT, help="SSH port (default: 22)")
    p.add_argument("-i", "--identity-file", metavar="PATH", help="SSH private key for the login user")
    p.add_argument("-o", "--ssh-option", action="append", metavar="OPTION", default=[],
                   help="extra ssh -o option, can be repeated")
    p.add_argument("-R", "--replace-host-key", action="store_true",
                   help="remove this host from known_hosts before connecting")

    s = p.add_subparsers(dest="ssh_action", metavar="<action>")
    s.required = True

    a = s.add_parser("adduser", parents=parents, aliases=["init", "provision-user"],
                     help="create/update a user, install SSH key, add sudo")
    _user_option(a, "new or existing Linux user to configure", required=True)
    a.add_argument("--pubkey", required=True, metavar="KEY_TEXT_OR_PATH",
                   help="public key text in quotes, or a path such as ~/.ssh/id_ed25519.pub")
    pw = a.add_mutually_exclusive_group()
    pw.add_argument("--password", metavar="PASSWORD",
                    help="set password from argument (less private; avoid shell history)")
    pw.add_argument("--password-stdin", action="store_true", help="read password from stdin")
    pw.add_argument("--no-password", action="store_true", help="do not set or change password")
    a.add_argument("--force-password-change", action="store_true", help="require password change at next login")
    a.set_defaults(func=cmd_adduser)

    a = s.add_parser("addkey", parents=parents, aliases=["key", "add-key"],
                     help="add another public key to an existing user")
    _user_option(a, "user to update (default: SSH login user)")
    a.add_argument("--pubkey", required=True, metavar="KEY_TEXT_OR_PATH",
                   help="public key text in quotes, or a path such as ~/.ssh/id_ed25519.pub")
    a.set_defaults(func=cmd_addkey)

    a = s.add_parser("fish", parents=parents, help="install fish and set the user's default shell")
    _user_option(a, "user to update (default: SSH login user)")
    a.set_defaults(func=cmd_fish)

    a = s.add_parser("nopasswd", parents=parents, help="allow the user to run sudo without a password")
    _user_option(a, "user to update (default: SSH login user)")
    a.set_defaults(func=cmd_nopasswd)

    a = s.add_parser("sudo", parents=parents, aliases=["sudo-test"], help="verify the target user can run sudo")
    _user_option(a, "user to check (default: SSH login user)")
    a.set_defaults(func=cmd_sudo)

    a = s.add_parser("status", parents=parents, help="show basic instance status (uptime, disk, memory)")
    a.set_defaults(func=cmd_status)

    a = s.add_parser("gpu", parents=parents, aliases=["gpu-info"],
                     help="check GPUs, NVIDIA driver, CUDA, and runtimes",
                     description=_GPU_DESCRIPTION, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.set_defaults(func=cmd_gpu)

    a = s.add_parser("tunnel", parents=parents, help="forward one or more remote ports locally",
                     epilog=_TUNNEL_EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("-L", "--forward", action="append", metavar="SPEC", default=[],
                   help="local forward; may be repeated")
    a.set_defaults(func=cmd_tunnel)

    a = s.add_parser("run", parents=parents, help="run a command on the instance: run -- COMMAND...",
                     usage="cctl ssh [USER@]HOST run -- COMMAND [ARG ...]")
    a.add_argument("command", nargs=argparse.REMAINDER, metavar="COMMAND",
                   help="command to run on the remote host (put -- before it)")
    a.set_defaults(func=cmd_run)
