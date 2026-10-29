#!/usr/bin/env bash
set -euo pipefail

PROGRAM="$(basename "$0")"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
HOST=""
LOGIN_USER="${USER:-}"
PORT="22"
IDENTITY_FILE=""
YES="0"
REPLACE_HOST_KEY="0"
SSH_OPTIONS=()

usage() {
  cat <<'EOF'
Usage:
  ./cctl [global options] [LOGIN_USER@]HOST <ssh-command> [command options]
  ./cctl <openstack-command> [command options]

Global options:
  -u, --user USER             Override SSH login user, alternative to USER@HOST
  -p, --port PORT             SSH port (default: 22)
  --identity-file PATH        SSH private key for the login user
  --ssh-option OPTION         Extra ssh option, can be repeated
  -R, --replace-host-key      Remove this host from known_hosts before connecting
  -y, --yes                   Do not ask for confirmation
  -h, --help                  Show help

SSH Commands:
  adduser                     Create/update a user, install SSH key, add sudo
  addkey                      Add another public key to an existing user
  fish                        Install fish and set the user's default shell
  nopasswd                    Allow the user to run sudo without a password
  sudo                        Verify the target user can run sudo
  gpu                         Check GPUs, NVIDIA driver, CUDA, and runtimes
  status                      Show basic instance status
  tunnel                      Forward one or more remote ports locally
  run -- COMMAND              Run a command on the instance

OpenStack Commands (Requires OpenStack RC):
  instances                   List instances across TACC and UC profiles
  launch                      Launch a new instance
  leases                      List leases across TACC and UC
  renew                       Renew a Chameleon Cloud lease

Examples:
  ./cctl cc@192.5.86.156 adduser -u llamacpp --pubkey ~/.ssh/id_ed25519.pub
  ./cctl llamacpp@192.5.86.156 fish
  ./cctl llamacpp@192.5.86.156 tunnel -L 8888 -L 6006
  ./cctl 192.5.86.156 run -- whoami
  ./cctl instances
  ./cctl instances --profile tacc
  ./cctl leases
  ./cctl renew my-lease --add-days 7
EOF
}

die() {
  echo "error: $*" >&2
  exit 1
}

info() {
  echo "==> $*" >&2
}

confirm() {
  local prompt="$1"
  if [[ "$YES" == "1" ]]; then
    return 0
  fi

  local answer
  read -r -p "$prompt [y/N] " answer
  [[ "$answer" == "y" || "$answer" == "Y" || "$answer" == "yes" || "$answer" == "YES" ]]
}

option_value() {
  local opt="$1"
  local value="${2-}"
  [[ -n "$value" ]] || die "$opt requires a value"
  printf "%s" "$value"
}

require_host() {
  [[ -n "$HOST" ]] || die "missing host"
}

require_login_user() {
  [[ -n "$LOGIN_USER" ]] || die "missing --user and local USER is not set"
}

set_host_arg() {
  local value="$1"
  [[ -n "$value" ]] || die "host cannot be empty"

  case "$value" in
    *@*)
      LOGIN_USER="${value%@*}"
      HOST="${value#*@}"
      [[ -n "$LOGIN_USER" ]] || die "login user in host cannot be empty"
      [[ -n "$HOST" ]] || die "host cannot be empty"
      ;;
    *)
      HOST="$value"
      ;;
  esac
}

validate_username() {
  local user="$1"
  [[ "$user" =~ ^[a-z_][a-z0-9_-]*[$]?$ ]] || die "invalid Linux username: $user"
}

shell_quote() {
  printf "'"
  printf "%s" "$1" | sed "s/'/'\\\\''/g"
  printf "'"
}

read_public_key() {
  local value="$1"
  if [[ -f "$value" ]]; then
    value="$(tr -d '\r\n' < "$value")"
  fi

  [[ -n "$value" ]] || die "public key is empty"
  case "$value" in
    ssh-rsa\ *|ssh-ed25519\ *|ecdsa-sha2-*\ *|sk-ssh-ed25519@openssh.com\ *|sk-ecdsa-sha2-nistp256@openssh.com\ *)
      printf "%s" "$value"
      ;;
    *)
      die "public key must be a valid OpenSSH public key string or a path to one"
      ;;
  esac
}

ssh_base_args() {
  local args=(-p "$PORT" -o BatchMode=no -o StrictHostKeyChecking=accept-new)
  if [[ -n "$IDENTITY_FILE" ]]; then
    args+=(-i "$IDENTITY_FILE")
  fi
  if ((${#SSH_OPTIONS[@]})); then
    args+=("${SSH_OPTIONS[@]}")
  fi
  printf '%s\0' "${args[@]}"
}

remote_target() {
  require_login_user
  printf "%s@%s" "$LOGIN_USER" "$HOST"
}

replace_known_host() {
  require_host
  command -v ssh-keygen >/dev/null 2>&1 || die "ssh-keygen is required for --replace-host-key"

  local target="$HOST"
  if [[ "$PORT" != "22" ]]; then
    target="[$HOST]:$PORT"
  fi

  if ! confirm "Remove old SSH host key entry for ${target}?"; then
    die "host key replacement cancelled"
  fi

  info "removing old SSH host key entry for ${target}"
  ssh-keygen -R "$target" >/dev/null
}

run_ssh() {
  require_host
  local args=()
  while IFS= read -r -d '' arg; do
    args+=("$arg")
  done < <(ssh_base_args)
  ssh "${args[@]}" "$(remote_target)" "$@"
}

run_ssh_tty() {
  require_host
  local args=()
  while IFS= read -r -d '' arg; do
    args+=("$arg")
  done < <(ssh_base_args)
  ssh -tt "${args[@]}" "$(remote_target)" "$@"
}

send_remote_script_tty() {
  require_host
  require_login_user
  local args=()
  while IFS= read -r -d '' arg; do
    args+=("$arg")
  done < <(ssh_base_args)

  local sudo_password script_b64 remote_cmd
  read -r -s -p "Sudo password for $(remote_target) (leave empty to try passwordless sudo): " sudo_password < /dev/tty
  echo > /dev/tty

  script_b64="$(base64)"
  remote_cmd="$(cat <<'REMOTE'
set -euo pipefail
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
REMOTE
)"
  {
    printf '%s\n' "$sudo_password"
    printf '%s\n' "$script_b64"
  } | ssh "${args[@]}" "$(remote_target)" "bash -c $(shell_quote "$remote_cmd")"
}

send_remote_user_script() {
  require_host
  local args=()
  while IFS= read -r -d '' arg; do
    args+=("$arg")
  done < <(ssh_base_args)
  ssh "${args[@]}" "$(remote_target)" "bash -s"
}

prompt_password() {
  local first second
  read -r -s -p "New password: " first
  echo >&2
  read -r -s -p "Confirm password: " second
  echo >&2
  [[ "$first" == "$second" ]] || die "passwords do not match"
  [[ -n "$first" ]] || die "password cannot be empty"
  printf "%s" "$first"
}

# Create or update a user, install SSH key, add sudo
cmd_provision_user() {
  local new_user=""
  local pubkey_arg=""
  local password=""
  local password_mode="prompt"
  local force_password_change="0"

  while (($#)); do
    case "$1" in
      -u|--user)
        new_user="$(option_value "$1" "${2-}")"
        shift 2
        ;;
      --pubkey)
        pubkey_arg="$(option_value "$1" "${2-}")"
        shift 2
        ;;
      --password)
        password="$(option_value "$1" "${2-}")"
        password_mode="value"
        shift 2
        ;;
      --password-stdin)
        password_mode="stdin"
        shift
        ;;
      --no-password)
        password_mode="none"
        shift
        ;;
      --force-password-change)
        force_password_change="1"
        shift
        ;;
      -h|--help)
        cat <<'EOF'
Usage:
  ./cctl [global options] [LOGIN_USER@]HOST adduser -u USER --pubkey KEY_TEXT_OR_PATH [options]

Options:
  -u, --user USER             New or existing Linux user to configure
  --pubkey KEY_TEXT_OR_PATH   Public key text in quotes, or a path such as ~/.ssh/id_ed25519.pub
  --password PASSWORD         Set password from argument (less private; avoid shell history)
  --password-stdin            Read password from stdin
  --no-password               Do not set or change password
  --force-password-change     Require password change at next login
EOF
        return 0
        ;;
      *)
        die "unknown adduser option: $1"
        ;;
    esac
  done

  require_host
  [[ -n "$new_user" ]] || die "missing --user"
  [[ -n "$pubkey_arg" ]] || die "missing --pubkey"
  validate_username "$new_user"

  local pubkey
  pubkey="$(read_public_key "$pubkey_arg")"

  case "$password_mode" in
    prompt)
      password="$(prompt_password)"
      ;;
    stdin)
      IFS= read -r password || true
      [[ -n "$password" ]] || die "password read from stdin is empty"
      ;;
    value)
      [[ -n "$password" ]] || die "--password cannot be empty"
      ;;
    none)
      password=""
      ;;
  esac

  echo "Target: $(remote_target)"
  echo "User:   $new_user"
  echo "Key:    ${pubkey%% *} ${pubkey##* }"
  if ! confirm "Provision this user on $HOST?"; then
    die "cancelled"
  fi

  info "connecting to $HOST as $LOGIN_USER"
  {
    printf 'NEW_USER=%s\n' "$(shell_quote "$new_user")"
    printf 'PUBLIC_KEY=%s\n' "$(shell_quote "$pubkey")"
    printf 'PASSWORD=%s\n' "$(shell_quote "$password")"
    printf 'FORCE_PASSWORD_CHANGE=%s\n' "$(shell_quote "$force_password_change")"
    cat <<'REMOTE'
set -euo pipefail

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
REMOTE
  } | send_remote_script_tty

  info "try: ssh ${new_user}@${HOST}"
}

# Add another public key to an existing user
cmd_add_key() {
  local target_user=""
  local pubkey_arg=""

  while (($#)); do
    case "$1" in
      -u|--user)
        target_user="$(option_value "$1" "${2-}")"
        shift 2
        ;;
      --pubkey)
        pubkey_arg="$(option_value "$1" "${2-}")"
        shift 2
        ;;
      -h|--help)
        cat <<'EOF'
Usage:
  ./cctl [global options] [LOGIN_USER@]HOST addkey [-u USER] --pubkey KEY_TEXT_OR_PATH

Options:
  -u, --user USER             User to update (default: SSH login user)
  --pubkey KEY_TEXT_OR_PATH   Public key text in quotes, or a path such as ~/.ssh/id_ed25519.pub
EOF
        return 0
        ;;
      *)
        die "unknown addkey option: $1"
        ;;
    esac
  done

  require_host
  require_login_user
  if [[ -z "$target_user" ]]; then
    target_user="$LOGIN_USER"
  fi
  [[ -n "$pubkey_arg" ]] || die "missing --pubkey"
  validate_username "$target_user"

  local pubkey
  pubkey="$(read_public_key "$pubkey_arg")"

  {
    printf 'TARGET_USER=%s\n' "$(shell_quote "$target_user")"
    printf 'PUBLIC_KEY=%s\n' "$(shell_quote "$pubkey")"
    cat <<'REMOTE'
set -euo pipefail
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
REMOTE
  } | send_remote_script_tty
}

# Verify the target user can run sudo
cmd_sudo_test() {
  local target_user=""
  while (($#)); do
    case "$1" in
      -u|--user)
        target_user="$(option_value "$1" "${2-}")"
        shift 2
        ;;
      -h|--help)
        cat <<'EOF'
Usage:
  ./cctl [global options] [LOGIN_USER@]HOST sudo [-u USER]

Options:
  -u, --user USER             User to check (default: SSH login user)
EOF
        return 0
        ;;
      *)
        die "unknown sudo option: $1"
        ;;
    esac
  done
  require_login_user
  if [[ -z "$target_user" ]]; then
    target_user="$LOGIN_USER"
  fi
  validate_username "$target_user"
  if [[ "$target_user" == "$LOGIN_USER" ]]; then
    run_ssh_tty "sudo -l && id"
  else
    run_ssh_tty "sudo -l -U $(shell_quote "$target_user") && id $(shell_quote "$target_user")"
  fi
}

# Install fish and set the user's default shell
cmd_fish() {
  local target_user=""
  while (($#)); do
    case "$1" in
      -u|--user)
        target_user="$(option_value "$1" "${2-}")"
        shift 2
        ;;
      -h|--help)
        cat <<'EOF'
Usage:
  ./cctl [global options] [LOGIN_USER@]HOST fish [-u USER]

Options:
  -u, --user USER             User to update (default: SSH login user)
EOF
        return 0
        ;;
      *)
        die "unknown fish option: $1"
        ;;
    esac
  done

  require_login_user
  if [[ -z "$target_user" ]]; then
    target_user="$LOGIN_USER"
  fi
  validate_username "$target_user"

  {
    printf 'TARGET_USER=%s\n' "$(shell_quote "$target_user")"
    cat <<'REMOTE'
set -euo pipefail

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
REMOTE
  } | send_remote_script_tty
}

# Allow the user to run sudo without a password
cmd_nopasswd() {
  local target_user=""
  while (($#)); do
    case "$1" in
      -u|--user)
        target_user="$(option_value "$1" "${2-}")"
        shift 2
        ;;
      -h|--help)
        cat <<'EOF'
Usage:
  ./cctl [global options] [LOGIN_USER@]HOST nopasswd [-u USER]

Options:
  -u, --user USER             User to update (default: SSH login user)
EOF
        return 0
        ;;
      *)
        die "unknown nopasswd option: $1"
        ;;
    esac
  done

  require_login_user
  if [[ -z "$target_user" ]]; then
    target_user="$LOGIN_USER"
  fi
  validate_username "$target_user"

  {
    printf 'TARGET_USER=%s\n' "$(shell_quote "$target_user")"
    cat <<'REMOTE'
set -euo pipefail

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
REMOTE
  } | send_remote_script_tty
}

# Show basic system information (hostname, uptime, uname, df, free).
cmd_status() {
  if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    cat <<'EOF_HELP'
Usage:
  ./cctl [global options] [LOGIN_USER@]HOST status

Shows basic instance status like uptime, disk space, and memory.
EOF_HELP
    return 0
  fi
  [[ "$#" -eq 0 ]] || die "status does not accept arguments"
  run_ssh "hostname; uptime; uname -a; df -h /; free -h 2>/dev/null || true"
}

# Check GPUs, NVIDIA driver, CUDA, and runtimes
cmd_gpu_info() {
  if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    cat <<'EOF'
Usage:
  ./cctl [global options] [LOGIN_USER@]HOST gpu

Checks:
  - GPU-like PCI devices
  - NVIDIA driver and nvidia-smi
  - NVIDIA kernel modules
  - CUDA toolkit and common CUDA libraries
  - Python packages with GPU support checks
  - Docker/NVIDIA container runtime presence
EOF
    return 0
  fi
  [[ "$#" -eq 0 ]] || die "gpu does not accept arguments"
  cat <<'REMOTE' | send_remote_user_script
set -u

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
REMOTE
}

# Run a specific command on the remote instance.
cmd_run() {
  if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    cat <<'EOF_HELP'
Usage:
  ./cctl [global options] [LOGIN_USER@]HOST run -- COMMAND

Runs a single command on the remote instance.
EOF_HELP
    return 0
  fi
  [[ "${1:-}" == "--" ]] && shift
  (($#)) || die "run requires a command"
  run_ssh "$@"
}

validate_tunnel_port() {
  local port="$1"
  [[ "$port" =~ ^[0-9]{1,5}$ ]] || die "invalid tunnel port: $port"
  ((10#$port >= 1 && 10#$port <= 65535)) || die "tunnel port must be between 1 and 65535: $port"
}

# Forward one or more local ports through the remote SSH server.
cmd_tunnel() {
  if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    cat <<'EOF_HELP'
Usage:
  ./cctl [global options] [LOGIN_USER@]HOST tunnel -L SPEC [-L SPEC ...]

Options:
  -L, --forward SPEC          Local forward; may be repeated

Forward formats:
  PORT
      Same-port shorthand for PORT:localhost:PORT.

  LOCAL_PORT:REMOTE_PORT
      Shorthand for LOCAL_PORT:localhost:REMOTE_PORT.

  LOCAL_PORT:REMOTE_HOST:REMOTE_PORT
  BIND_ADDRESS:LOCAL_PORT:REMOTE_HOST:REMOTE_PORT
      Standard OpenSSH local-forward formats.

Examples:
  ./cctl user@host tunnel -L 8888
  ./cctl user@host tunnel -L 8888:8888
  ./cctl user@host tunnel -L 8888:localhost:8888 -L 6006:localhost:6006
  ./cctl user@host tunnel -L 127.0.0.1:15432:db.internal:5432

The tunnel runs in the foreground. Press Ctrl-C to close all forwards.
EOF_HELP
    return 0
  fi

  require_host
  require_login_user

  local forwards=()
  local spec local_port remote_port
  while (($#)); do
    case "$1" in
      -L|--forward)
        spec="$(option_value "$1" "${2-}")"
        shift 2

        if [[ "$spec" =~ ^[0-9]+$ ]]; then
          validate_tunnel_port "$spec"
          spec="${spec}:localhost:${spec}"
        elif [[ "$spec" =~ ^([0-9]+):([0-9]+)$ ]]; then
          local_port="${BASH_REMATCH[1]}"
          remote_port="${BASH_REMATCH[2]}"
          validate_tunnel_port "$local_port"
          validate_tunnel_port "$remote_port"
          spec="${local_port}:localhost:${remote_port}"
        elif [[ "$spec" != *:*:* ]]; then
          die "invalid forward '$spec'; expected PORT, LOCAL_PORT:REMOTE_PORT, or an OpenSSH -L specification"
        fi

        forwards+=("$spec")
        ;;
      *)
        die "unknown tunnel option: $1"
        ;;
    esac
  done

  ((${#forwards[@]})) || die "tunnel requires at least one -L or --forward"

  local args=(-N -T -o ExitOnForwardFailure=yes)
  local arg
  while IFS= read -r -d '' arg; do
    args+=("$arg")
  done < <(ssh_base_args)

  info "opening SSH tunnel to $(remote_target)"
  for spec in "${forwards[@]}"; do
    info "forward: $spec"
    args+=(-L "$spec")
  done
  info "press Ctrl-C to close the tunnel"

  ssh "${args[@]}" "$(remote_target)"
}

# Find an RC file without exposing or importing its credentials into this shell.
# User config takes precedence over RC files kept beside this script.
profile_rc_path() {
  local profile="$1"
  [[ "$profile" =~ ^[a-zA-Z0-9_-]+$ ]] || return 1

  if [[ -n "${CCTL_CONFIG_DIR:-}" ]]; then
    [[ -f "$CCTL_CONFIG_DIR/$profile.sh" ]] || return 1
    printf '%s\n' "$CCTL_CONFIG_DIR/$profile.sh"
    return 0
  fi

  local user_config_dir=""
  if [[ -n "${XDG_CONFIG_HOME:-}" ]]; then
    user_config_dir="$XDG_CONFIG_HOME/cctl"
  elif [[ -n "${HOME:-}" ]]; then
    user_config_dir="$HOME/.config/cctl"
  fi

  if [[ -n "$user_config_dir" && -f "$user_config_dir/$profile.sh" ]]; then
    printf '%s\n' "$user_config_dir/$profile.sh"
  elif [[ -f "$SCRIPT_DIR/$profile.sh" ]]; then
    printf '%s\n' "$SCRIPT_DIR/$profile.sh"
  else
    return 1
  fi
}

# Run a callback with one profile in a subshell so its OS_* variables cannot
# overwrite another profile or leak back into the caller's environment.
run_openstack_from_rc() (
  local profile="$1"
  local rc_file="$2"
  local callback="$3"
  shift 3

  set +u
  # shellcheck disable=SC1090
  source "$rc_file"
  set -u

  [[ -n "${OS_AUTH_URL:-}" ]] || die "$rc_file did not set OS_AUTH_URL"
  "$callback" "$profile" "$@"
)

# Run a read-only OpenStack callback for the selected profile or all default
# profiles. If no profile files exist, fall back to an already sourced RC.
run_openstack_profiles() {
  local selection="$1"
  local action="$2"
  local callback="$3"
  shift 3

  if [[ "$selection" == "current" ]]; then
    [[ -n "${OS_AUTH_URL:-}" ]] || die "no OpenStack environment is active; source an RC file or configure cctl profiles"
    "$callback" "current" "$@"
    return
  fi

  local profiles=()
  if [[ "$selection" == "all" ]]; then
    profiles=(tacc uc)
  else
    profiles=("$selection")
  fi

  local profile rc_file
  local found=0
  local failed=0
  for profile in "${profiles[@]}"; do
    if ! rc_file="$(profile_rc_path "$profile")"; then
      if [[ "$selection" != "all" ]]; then
        die "profile '$profile' was not found (expected $profile.sh)"
      fi
      continue
    fi

    if ((found)); then
      printf '\n'
    fi
    found=1
    if ! run_openstack_from_rc "$profile" "$rc_file" "$callback" "$@"; then
      info "failed to $action for profile '$profile'"
      failed=1
    fi
  done

  if ((!found)); then
    if [[ -n "${OS_AUTH_URL:-}" ]]; then
      "$callback" "current" "$@"
      return
    fi
    die "no profiles found; add tacc.sh and uc.sh beside cctl or under ~/.config/cctl"
  fi

  return "$failed"
}

list_instances_for_active_profile() {
  local profile="$1"
  shift
  printf '\033[96m==> Profile: %s | Chameleon Region: %s\033[0m\n' \
    "$profile" "${OS_REGION_NAME:-Unknown}"
  openstack server list "$@"
}

# List Chameleon Cloud (OpenStack) instances
cmd_instances() {
  if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    cat <<'EOF_HELP'
Usage:
  ./cctl instances [--profile all|NAME] [--current] [openstack server list options]

By default, queries both the tacc and uc profiles when their RC files exist.
Each profile is loaded in an isolated subshell, so credentials do not overwrite
one another. Use --profile NAME for one RC file or --current to use an RC file
already sourced by the calling shell.

RC file lookup:
  $CCTL_CONFIG_DIR/NAME.sh (when CCTL_CONFIG_DIR is set)
  ~/.config/cctl/NAME.sh
  NAME.sh beside the cctl script
EOF_HELP
    return 0
  fi
  command -v openstack >/dev/null 2>&1 || die "openstack CLI is required. Try: pip install python-openstackclient"

  local selection="all"
  local openstack_args=()
  while (($#)); do
    case "$1" in
      --profile)
        selection="$(option_value "$1" "${2-}")"
        shift 2
        ;;
      --current)
        selection="current"
        shift
        ;;
      --)
        shift
        openstack_args+=("$@")
        break
        ;;
      *)
        openstack_args+=("$1")
        shift
        ;;
    esac
  done

  run_openstack_profiles "$selection" "list instances" \
    list_instances_for_active_profile "${openstack_args[@]}"
}

# Launch a new Chameleon Cloud (OpenStack) instance
cmd_launch() {
  if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    cat <<'EOF_HELP'
Usage:
  ./cctl launch [options]

Wrapper for creating a new instance. Requires OpenStack RC environment variables.
Examples:
  ./cctl launch --name my-instance --flavor m1.medium --image CC-Ubuntu20.04 --key-name mykey
EOF_HELP
    return 0
  fi
  command -v openstack >/dev/null 2>&1 || die "openstack CLI is required. Try: pip install python-openstackclient"
  echo -e "[96m==> Active Chameleon Region: ${OS_REGION_NAME:-Unknown}[0m"
  openstack server create "$@"
}

# Find an OpenStack CLI that includes the Blazar reservation plugin.
find_blazar_openstack() {
  local primary=""
  primary="$(command -v openstack 2>/dev/null || true)"

  if [[ -n "${CCTL_OPENSTACK_BIN:-}" ]]; then
    [[ -x "$CCTL_OPENSTACK_BIN" ]] || die "CCTL_OPENSTACK_BIN is not executable: $CCTL_OPENSTACK_BIN"
    if "$CCTL_OPENSTACK_BIN" reservation lease list --help >/dev/null 2>&1; then
      printf '%s\n' "$CCTL_OPENSTACK_BIN"
      return 0
    fi
    die "CCTL_OPENSTACK_BIN does not include the Blazar reservation plugin: $CCTL_OPENSTACK_BIN"
  fi

  if [[ -n "$primary" ]] && "$primary" reservation lease list --help >/dev/null 2>&1; then
    printf '%s\n' "$primary"
    return 0
  fi

  local user_openstack=""
  if [[ -n "${HOME:-}" ]]; then
    user_openstack="$HOME/.local/bin/openstack"
  fi
  if [[ -n "$user_openstack" && "$user_openstack" != "$primary" && -x "$user_openstack" ]] && \
      "$user_openstack" reservation lease list --help >/dev/null 2>&1; then
    printf '%s\n' "$user_openstack"
    return 0
  fi

  die "OpenStack Blazar plugin is required. Install it with: uv tool install python-openstackclient --with python-blazarclient --force"
}

# List leases for the active OpenStack environment with expiration coloring.
list_leases_for_active_profile() {
  local profile="$1"
  local openstack_bin="$2"
  printf '\033[96m==> Profile: %s | Chameleon Region: %s\033[0m\n' \
    "$profile" "${OS_REGION_NAME:-Unknown}"

  python3 - "$openstack_bin" <<'PY'
import json, subprocess, datetime, sys

openstack_bin = sys.argv[1]

try:
    res = subprocess.run([openstack_bin, "reservation", "lease", "list", "-f", "json"], capture_output=True, text=True, check=True)
except subprocess.CalledProcessError as e:
    message = (e.stderr or "").strip() or (e.stdout or "").strip()
    if message:
        print(message, file=sys.stderr)
    else:
        print(f"OpenStack lease query failed with exit code {e.returncode}.", file=sys.stderr)
    sys.exit(e.returncode)
except FileNotFoundError:
    print("Error: openstack command not found.", file=sys.stderr)
    sys.exit(1)

try:
    leases = json.loads(res.stdout)
except json.JSONDecodeError:
    print("Error parsing JSON output from openstack.", file=sys.stderr)
    sys.exit(1)

if not leases:
    print("No leases found.")
    sys.exit(0)

def get_key(d, key):
    for k in d.keys():
        if k.lower() == key.lower(): return d[k]
    return "N/A"

print(f"\n{'Name':<35} | {'Start Date':<20} | {'End Date':<20} | {'Status':<10}")
print("-" * 94)

now_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

for lease in leases:
    name = str(get_key(lease, "name"))[:35]
    start = str(get_key(lease, "start_date"))
    end = str(get_key(lease, "end_date"))
    
    end_cmp = end.replace(" ", "T")
    
    if end_cmp != "N/A" and end_cmp < now_str:
        color = "\033[91m" # Red
        status = "Expired"
    else:
        color = "\033[92m" # Green
        status = "Active"
        
    print(f"{color}{name:<35} | {start:<20} | {end:<20} | {status:<10}\033[0m")
print("")
PY
}

# List Chameleon Cloud leases (reservations) across configured profiles.
cmd_leases() {
  if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    cat <<'EOF_HELP'
Usage:
  ./cctl leases [--profile all|NAME] [--current]

By default, lists OpenStack Blazar leases from both tacc and uc profiles.
Leases are colored red if expired and green if active. Use --profile NAME for
one RC file or --current to use an RC file already sourced by the calling shell.
EOF_HELP
    return 0
  fi
  local openstack_bin
  openstack_bin="$(find_blazar_openstack)"

  local selection="all"
  while (($#)); do
    case "$1" in
      --profile)
        selection="$(option_value "$1" "${2-}")"
        shift 2
        ;;
      --current)
        selection="current"
        shift
        ;;
      *)
        die "unknown leases option: $1"
        ;;
    esac
  done

  run_openstack_profiles "$selection" "list leases" \
    list_leases_for_active_profile "$openstack_bin"
}

# Return success when an exact lease name or ID exists in one profile. A query
# failure is distinct from "not found" so auto-detection never writes after an
# incomplete cross-profile search.
lease_exists_from_rc() (
  local profile="$1"
  local rc_file="$2"
  local openstack_bin="$3"
  local lease_name="$4"

  set +u
  # shellcheck disable=SC1090
  source "$rc_file"
  set -u

  if [[ -z "${OS_AUTH_URL:-}" ]]; then
    echo "error: $rc_file did not set OS_AUTH_URL" >&2
    return 2
  fi

  python3 - "$openstack_bin" "$lease_name" "$profile" <<'PY'
import json
import subprocess
import sys

openstack_bin, lease_name, profile = sys.argv[1:]

try:
    result = subprocess.run(
        [openstack_bin, "reservation", "lease", "list", "-f", "json"],
        capture_output=True,
        text=True,
        check=True,
    )
except subprocess.CalledProcessError as exc:
    message = (exc.stderr or "").strip() or (exc.stdout or "").strip()
    print(message or f"Lease lookup failed for profile {profile}.", file=sys.stderr)
    sys.exit(2)

try:
    leases = json.loads(result.stdout)
except json.JSONDecodeError:
    print(f"Could not parse the lease list for profile {profile}.", file=sys.stderr)
    sys.exit(2)

def value(item, wanted):
    for key, item_value in item.items():
        if key.lower() == wanted:
            return str(item_value)
    return ""

matches = [
    lease for lease in leases
    if value(lease, "name") == lease_name or value(lease, "id") == lease_name
]

if len(matches) == 1:
    sys.exit(0)
if not matches:
    sys.exit(1)

print(f"Lease '{lease_name}' appears more than once in profile {profile}.", file=sys.stderr)
sys.exit(3)
PY
)

resolve_lease_profile() {
  local lease_name="$1"
  local openstack_bin="$2"
  local profile rc_file status
  local found=0
  local matches=()
  local missing=()

  for profile in tacc uc; do
    if ! rc_file="$(profile_rc_path "$profile")"; then
      missing+=("$profile")
      continue
    fi
    found=1

    if lease_exists_from_rc "$profile" "$rc_file" "$openstack_bin" "$lease_name"; then
      matches+=("$profile")
    else
      status=$?
      if [[ "$status" -ne 1 ]]; then
        info "could not safely locate lease '$lease_name' across all profiles"
        return "$status"
      fi
    fi
  done

  if ((!found)); then
    if [[ -n "${OS_AUTH_URL:-}" ]]; then
      printf '%s\n' "current"
      return 0
    fi
    die "no profiles found; add tacc.sh and uc.sh under ~/.config/cctl"
  fi

  if ((${#missing[@]})); then
    die "cannot auto-detect a lease while profiles are missing (${missing[*]}); restore them or use --profile NAME"
  fi

  case "${#matches[@]}" in
    0)
      die "lease '$lease_name' was not found in the tacc or uc profile"
      ;;
    1)
      printf '%s\n' "${matches[0]}"
      ;;
    *)
      die "lease '$lease_name' exists in multiple profiles (${matches[*]}); rerun with --profile tacc or --profile uc"
      ;;
  esac
}

renew_lease_for_active_profile() {
  local profile="$1"
  local openstack_bin="$2"
  local lease_name="$3"
  local add_days="$4"
  shift 4

  printf '\033[96m==> Profile: %s | Chameleon Region: %s\033[0m\n' \
    "$profile" "${OS_REGION_NAME:-Unknown}"

  if [[ -z "$add_days" ]]; then
    "$openstack_bin" reservation lease set "$lease_name" "$@"
    return
  fi

  python3 - "$openstack_bin" "$lease_name" "$add_days" <<'PY'
import datetime
import json
import subprocess
import sys

openstack_bin, lease_name, add_days_text = sys.argv[1:]
add_days = int(add_days_text)

try:
    result = subprocess.run(
        [openstack_bin, "reservation", "lease", "show", lease_name, "-f", "json"],
        capture_output=True,
        text=True,
        check=True,
    )
except subprocess.CalledProcessError as exc:
    message = (exc.stderr or "").strip() or (exc.stdout or "").strip()
    print(message or f"Failed to fetch lease '{lease_name}'.", file=sys.stderr)
    sys.exit(exc.returncode)

try:
    lease = json.loads(result.stdout)
except json.JSONDecodeError:
    print(f"Could not parse lease details for '{lease_name}'.", file=sys.stderr)
    sys.exit(1)

end_date_text = next(
    (str(value) for key, value in lease.items() if key.lower() == "end_date"),
    "",
)
if not end_date_text:
    print(f"Could not determine the end date for '{lease_name}'.", file=sys.stderr)
    sys.exit(1)

normalized_end = end_date_text.split(".")[0].replace(" ", "T")
current_end = datetime.datetime.strptime(normalized_end, "%Y-%m-%dT%H:%M:%S")
new_end = current_end + datetime.timedelta(days=add_days)
new_end_text = new_end.strftime("%Y-%m-%d %H:%M")

print(f"==> Extending lease '{lease_name}' by {add_days} days (new end: {new_end_text})", flush=True)

try:
    subprocess.run(
        [openstack_bin, "reservation", "lease", "set", lease_name, "--end-date", new_end_text],
        check=True,
    )
except subprocess.CalledProcessError as exc:
    print("Failed to extend the lease. Check Chameleon's renewal window and duration limits.", file=sys.stderr)
    sys.exit(exc.returncode)

print("==> Lease successfully extended.")
PY
}

# Renew a Chameleon Cloud lease, auto-detecting its profile when unambiguous.
cmd_renew() {
  if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
    cat <<'EOF_HELP'
Usage:
  ./cctl renew <lease_name_or_id> [--profile NAME | --current] [options]

By default, finds an exact lease name or ID across tacc and uc profiles and
renews it only when exactly one profile matches. If both profiles contain the
same name, select one explicitly.

The target is a Blazar lease name shown by `cctl leases`, not a Nova server name
shown by `cctl instances`.

Options:
  --profile NAME                  Use one configured profile (tacc or uc)
  --current                       Use the OpenStack environment already sourced
  --end-date "YYYY-MM-DD HH:MM"   Set an absolute new end date
  --add-days N                    Extend from the current end date by N days

Examples:
  ./cctl renew my-lease --add-days 7
  ./cctl renew my-lease --profile tacc --add-days 7
  ./cctl renew my-lease --profile uc --end-date "2026-08-11 23:59"
EOF_HELP
    return 0
  fi

  (($#)) || die "missing lease name or id. Try: ./cctl renew --help"

  local lease_name="$1"
  shift

  local selection="auto"
  local add_days=""
  local other_args=()
  while (($#)); do
    case "$1" in
      --profile)
        selection="$(option_value "$1" "${2-}")"
        shift 2
        ;;
      --current)
        selection="current"
        shift
        ;;
      --add-days)
        add_days="$(option_value "$1" "${2-}")"
        shift 2
        ;;
      *)
        other_args+=("$1")
        shift
        ;;
    esac
  done

  if [[ -n "$add_days" ]]; then
    [[ "$add_days" =~ ^[1-9][0-9]*$ ]] || die "--add-days must be a positive integer"
    ((${#other_args[@]} == 0)) || die "--add-days cannot be combined with other lease-set options"
  fi

  [[ "$selection" != "all" ]] || die "renew requires one profile; use --profile tacc or --profile uc"

  local openstack_bin
  openstack_bin="$(find_blazar_openstack)"

  local resolved_profile rc_file
  if [[ "$selection" == "auto" ]]; then
    resolved_profile="$(resolve_lease_profile "$lease_name" "$openstack_bin")"
  else
    resolved_profile="$selection"
  fi

  if [[ "$resolved_profile" == "current" ]]; then
    [[ -n "${OS_AUTH_URL:-}" ]] || die "no OpenStack environment is active; source an RC file or omit --current"
    renew_lease_for_active_profile "current" "$openstack_bin" \
      "$lease_name" "$add_days" "${other_args[@]}"
    return
  fi

  rc_file="$(profile_rc_path "$resolved_profile")" || \
    die "profile '$resolved_profile' was not found (expected $resolved_profile.sh)"
  run_openstack_from_rc "$resolved_profile" "$rc_file" \
    renew_lease_for_active_profile "$openstack_bin" "$lease_name" \
    "$add_days" "${other_args[@]}"
}

parse_global_options() {
  while (($#)); do
    case "$1" in
      -u|--user)
        LOGIN_USER="$(option_value "$1" "${2-}")"
        shift 2
        ;;
      -p|--port)
        PORT="$(option_value "$1" "${2-}")"
        shift 2
        ;;
      --identity-file)
        IDENTITY_FILE="$(option_value "$1" "${2-}")"
        shift 2
        ;;
      --ssh-option)
        SSH_OPTIONS+=("-o" "$(option_value "$1" "${2-}")")
        shift 2
        ;;
      -R|--replace-host-key)
        REPLACE_HOST_KEY="1"
        shift
        ;;
      -y|--yes)
        YES="1"
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      adduser|addkey|fish|nopasswd|init|key|sudo|gpu|status|tunnel|run|provision-user|add-key|sudo-test|gpu-info|instances|launch|leases|renew)
        COMMAND="$1"
        shift
        COMMAND_ARGS=("$@")
        return 0
        ;;
      --)
        shift
        (($#)) || die "missing host"
        [[ -z "$HOST" ]] || die "unexpected argument after host: $1"
        set_host_arg "$1"
        shift
        ;;
      -*)
        die "unknown option: $1"
        ;;
      *)
        [[ -z "$HOST" ]] || die "unknown option or command: $1"
        set_host_arg "$1"
        shift
        ;;
    esac
  done
  usage
  exit 1
}

COMMAND=""
COMMAND_ARGS=()
parse_global_options "$@"

if [[ "$REPLACE_HOST_KEY" == "1" ]]; then
  replace_known_host
fi

case "$COMMAND" in
  adduser|init|provision-user)
    cmd_provision_user "${COMMAND_ARGS[@]}"
    ;;
  addkey|key|add-key)
    cmd_add_key "${COMMAND_ARGS[@]}"
    ;;
  fish)
    cmd_fish "${COMMAND_ARGS[@]}"
    ;;
  nopasswd)
    cmd_nopasswd "${COMMAND_ARGS[@]}"
    ;;
  sudo|sudo-test)
    cmd_sudo_test "${COMMAND_ARGS[@]}"
    ;;
  gpu|gpu-info)
    cmd_gpu_info "${COMMAND_ARGS[@]}"
    ;;
  status)
    cmd_status "${COMMAND_ARGS[@]}"
    ;;
  tunnel)
    cmd_tunnel "${COMMAND_ARGS[@]}"
    ;;
  run)
    cmd_run "${COMMAND_ARGS[@]}"
    ;;
  instances)
    cmd_instances "${COMMAND_ARGS[@]}"
    ;;
  launch)
    cmd_launch "${COMMAND_ARGS[@]}"
    ;;
  leases)
    cmd_leases "${COMMAND_ARGS[@]}"
    ;;
  renew)
    cmd_renew "${COMMAND_ARGS[@]}"
    ;;
  *)
    die "unknown command: $COMMAND"
    ;;
esac
