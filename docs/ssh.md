# SSH instance management

All SSH helpers live under `cctl ssh [USER@]HOST <action>`. The login user defaults to
`cc` (the account on Chameleon images). Options placed before the action: `-u USER`,
`-p PORT`, `-i KEYFILE`, `-o SSHOPTION` (repeatable), `-R` (drop the old host key first).

```sh
# create your own user with your key, passwordless sudo
cctl ssh cc@IP adduser --user me --pubkey ~/.ssh/id_ed25519.pub --no-password

# add another key to an existing user
cctl ssh cc@IP addkey --user me --pubkey ~/.ssh/other.pub

# fish shell, sudo checks
cctl ssh me@IP fish
cctl ssh me@IP nopasswd
cctl ssh me@IP sudo

# status / GPU report / arbitrary command
cctl ssh me@IP status
cctl ssh me@IP gpu
cctl ssh me@IP run -- 'df -h; nvidia-smi'
```

Commands that modify the instance ask for confirmation (`-y` skips) and may prompt for the
login user's sudo password; the password is sent over stdin to `sudo -S`, never placed on a
command line.
