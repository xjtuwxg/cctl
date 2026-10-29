# SSH tunnels

Forward one or more remote ports through one SSH connection:

```sh
cctl ssh me@IP tunnel -L 8888 -L 6006        # local 8888 -> remote 8888, 6006 -> 6006
cctl ssh me@IP tunnel -L 9000:8080           # local 9000 -> remote 8080
```

`-L` accepts `PORT`, `LOCAL:REMOTE`, or a full OpenSSH spec. The tunnel stays in the
foreground (`ssh -N`); press Ctrl-C to close it.
