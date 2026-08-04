# SSH tunnels and port forwarding

The `tunnel` command forwards one or more services through a single SSH connection. It runs in the foreground so its lifetime is visible and easy to control.

## Quick start

Forward remote port `8888` to the same port on your computer:

```sh
./cctl USER@HOST tunnel -L 8888
```

This maps local `localhost:8888` to port `8888` on the Chameleon instance. You can now reach the remote service at `127.0.0.1:8888`. Press `Ctrl-C` to close the tunnel.

## Forward several ports

Repeat `-L` or `--forward` for every service. All forwards share one SSH connection:

```sh
./cctl USER@HOST tunnel \
  -L 8888 \
  -L 6006 \
  --forward 7860
```

If any local port cannot be opened, `cctl` exits instead of leaving a partially working tunnel.

## Forward formats

| Format | Meaning |
| --- | --- |
| `PORT` | Map local `PORT` to the same port on the SSH server |
| `LOCAL_PORT:REMOTE_PORT` | Forward to `localhost:REMOTE_PORT` on the SSH server |
| `LOCAL_PORT:REMOTE_HOST:REMOTE_PORT` | Forward to another host reachable from the SSH server |
| `BIND_ADDRESS:LOCAL_PORT:REMOTE_HOST:REMOTE_PORT` | Explicitly select the local bind address |

The destination always reads from left to right: **local port first, remote host and port second**. The short forms are expanded automatically, so these commands are equivalent:

```sh
./cctl USER@HOST tunnel -L 8888
./cctl USER@HOST tunnel -L 8888:8888
./cctl USER@HOST tunnel -L 8888:localhost:8888
```

`REMOTE_HOST` is resolved from the remote SSH server, not from your computer. For example, this exposes a private database as local port `15432`:

```sh
./cctl USER@BASTION tunnel -L 15432:db.internal:5432
```

## Binding and security

OpenSSH binds a local forward to the loopback interface by default. Other computers cannot connect to it.

You can make the bind address explicit:

```sh
./cctl USER@HOST tunnel -L 127.0.0.1:8888:localhost:8888
```

Avoid binding to `0.0.0.0` unless you intentionally want to expose the remote service to other machines and have reviewed local firewall and authentication settings.

## SSH connection options

Global SSH options go before `tunnel`. For example, use a private key and a non-default SSH server port:

```sh
./cctl \
  --identity-file ~/.ssh/chameleon_key \
  --port 2222 \
  USER@HOST tunnel -L 8888
```

For a long-running connection, SSH keepalives can help detect a broken network path:

```sh
./cctl \
  --ssh-option ServerAliveInterval=30 \
  --ssh-option ServerAliveCountMax=3 \
  USER@HOST tunnel -L 8888
```

## Troubleshooting

### Local port is already in use

Choose a different local port while keeping the remote port unchanged:

```sh
./cctl USER@HOST tunnel -L 18888:8888
```

Then connect to `127.0.0.1:18888`.

### The tunnel connects but the service does not respond

Check from the remote instance that the service is running and listening on the expected address and port:

```sh
./cctl USER@HOST run -- curl -I http://localhost:8888
```

If the service runs in a container, verify that its port is published or reachable from the remote host.

### SSH disconnects

The forwards close when the SSH connection closes. Run the command again after restoring network access; use the keepalive options above when idle connections are frequently dropped.
