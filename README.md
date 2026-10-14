# Minimal SSH-managed NixOS-WSL

Минимальная публичная база для первого запуска WSL и последующего обычного
NixOS deployment с другого Linux-компьютера по LAN/VPN. Подготовлена локально;
GitHub-репозиторий и remote revision **ещё не опубликованы**.

## Контракт

- **D4 — база:** NixOS-WSL, пользователь `swayf`, Nix/flakes, systemd, OpenSSH,
  Git, age/SOPS CLI. Нет Home Manager, Podman, gcnt, Playwright, Mihomo,
  ciphertext, личных ресурсов или обязательных application credentials.
- **D5 — identity позже:** key directory создаётся 0700; приватные SSH/age keys
  не создаются, не копируются и не читаются при evaluation/build. Оператор
  доставляет age identity самостоятельно; она нужна только secret-consuming профилю.
- **D6 — deployment:** `nixosModules.default` — общая operator management-база
  для ВСЕХ следующих профилей; `nixosModules.management` — универсальный
  механизм для caller-owned WSL/user/public keys. Обновление — обычный
  `nixos-rebuild --target-host ... --sudo`, без специального exporter.

Ваш публичный клиентский ключ: **RSA 3072**, fingerprint
`SHA256:5X/MGOxI4MYB+q/B/L6ZOjzCRDfg36uBrljsZt+109c`, comment `swayf@precision2`.
Это ключ КЛИЕНТА, не host key SSH-сервера. RSA-SHA2 поддерживается современным
OpenSSH; legacy SHA-1 `ssh-rsa` не включается. Приватного ключа здесь нет.

**R1 — административный доступ.** Этот SSH-ключ допускает вход как `swayf` с
passwordless sudo и доверием Nix daemon для приёма построенного closure. Это
root-equivalent deployment-доступ, не sandbox-пользователь. SSH password,
keyboard-interactive, root login и agent/TCP forwarding запрещены. Другие
пользователи wheel не получают passwordless sudo этой базой.

**R2 — Windows routing.** NixOS открывает только guest SSH port 22. Windows
firewall/portproxy меняются отдельной явной admin-командой, не activation.
NAT listener привязан к выбранному Windows IPv4:2222, не `0.0.0.0`.
Требуется явный список IP/CIDR Linux-клиентов; предпочтительны отдельные /32.
VPN должен уже доставлять трафик к этому Windows IP. База не устанавливает VPN,
не открывает router/WAN порты и не меняет глобальный `.wslconfig`.

**R3 — доступность.** Windows и выбранная WSL должны работать. SSH не поднимает
остановленную WSL; после смены её NAT IP повторить `expose-ssh.ps1`.
Профили не должны удалять management module, менять user/SSH port или закрывать
SSH. У каждого профиля полноценная NixOS-конфигурация, не независимый install-слой.

## Файлы

- `flake.nix` / `flake.lock`: только caller-pinned nixpkgs и NixOS-WSL;
  pins сохранены от проверенного существующего WSL snapshot.
- `host.nix`, `ssh-client.pub`: публичная operator декларация.
- `modules/management.nix`: общая SSH/admin-база, без fleet imports.
- `scripts/install.ps1`: fresh first install из проверенного rootfs и
  `github:owner/repo/<40-character-commit>#wsl-base`; существующая distro отклоняется.
- `scripts/expose-ssh.ps1`: NAT exposure/refresh/удаление одного owned endpoint.
- `tests/`: Nix contracts, Bash syntax, public-source boundaries,
  PowerShell parser/native mocks (если `pwsh` доступен). Они не запускают реальный WSL.

## A1 — проверить и опубликовать

На доверенном Linux:

```bash
cd nixos-wsl-base
ssh-keygen -lf ssh-client.pub
nix flake check --offline --no-write-lock-file --no-build path:.
nix build --no-write-lock-file --no-link path:.#checks.x86_64-linux.management
python3 tests/test-scripts.py
nix build --no-link --no-write-lock-file path:.#nixosConfigurations.wsl-base.config.system.build.toplevel
```

Проверить исходники перед публикацией. Репозиторий намеренно не содержит rootfs,
age identity, ciphertext или private fleet. Публичный SSH key не является
секретом, но раскрывает operator comment/fingerprint — его публикация осознанна.

Создать публичный GitHub repository (например, `nixos-wsl-base`), затем
**отдельным действием владельца** commit/push. Не вставлять токен в URL:

```bash
git init
git add .gitignore README.md flake.nix flake.lock host.nix ssh-client.pub modules scripts tests
git commit -m 'Add minimal SSH-managed NixOS-WSL base'
git branch -M main
git remote add origin https://github.com/YOUR_ACCOUNT/nixos-wsl-base.git
git push -u origin main
git rev-parse HEAD
```

Сохранить точный опубликованный commit из последней команды; не использовать
`main`, `latest` или moving ref для установки. Передать Windows bootstrap из
доверенной проверенной копии этого commit. Скрипт с административными полномочиями
также требует доверия, а checksum rootfs не проверяет происхождение самого скрипта.

## A2 — fresh first install, Windows

Понадобятся x86_64 Windows, актуальный WSL2/WSLg, виртуализация и сеть до GitHub/
Nix caches. Rootfs не входит в публичный Git. Проверенный initial rootfs —
upstream release **2605.7.2**, asset `nixos.wsl`, SHA256:

```text
e7180ad555fdcb8e1e057e2ef056de467603a5e502ff8531053738371be3f6b9
```

Источник: https://github.com/nix-community/NixOS-WSL/releases/tag/2605.7.2
и HTTPS GitHub API asset digest. Он bootstrap-only: installed system использует
flake pins, а не версии пакетов initial rootfs. Cold-cache target build не проверен.

Перенести rootfs и `scripts/` в Windows, например `C:\Deployments\nixos-wsl-base`.
PowerShell **пользователя-владельца WSL**, не чужой administrator account:

```powershell
wsl --version
wsl --list --verbose
Set-Location C:\Deployments\nixos-wsl-base

.\scripts\install.ps1 `
  -Flake 'github:YOUR_ACCOUNT/nixos-wsl-base/PUBLISHED_40_CHARACTER_COMMIT#wsl-base' `
  -ImagePath .\nixos.wsl `
  -ImageSha256 'e7180ad555fdcb8e1e057e2ef056de467603a5e502ff8531053738371be3f6b9' `
  -Distribution NixOS-Managed `
  -RestartDistribution

wsl -d NixOS-Managed -u swayf
```

Заменить owner/commit НАСТОЯЩИМИ значениями. `NixOS-Managed` — новый отдельный
экземпляр; существующие WSL-дистрибуции не затрагиваются. Скрипт
проверяет rootfs SHA256, отказывает на существующую distro/non-empty destination,
собирает pinned flake, проверяет ключевой-only SSH и **применяет базовую систему**.
При ошибке сохраняет distro для диагностики; не удалять/reimport вслепую.
`-RestartDistribution` явно завершает только новую distro.

## A3 — открыть SSH только выбранному Linux-клиенту

Выполнить в PowerShell **администратора того же Windows-пользователя**, у которого
создана WSL. Пример: Windows LAN IP `192.168.1.50`, Linux IP `192.168.1.10`:

```powershell
.\scripts\expose-ssh.ps1 `
  -Distribution NixOS-Managed `
  -ListenAddress 192.168.1.50 -ListenPort 2222 `
  -AllowedRemoteAddress 192.168.1.10
```

Адреса заменить реальными. Для VPN использовать назначенный WINDOWS VPN IPv4
в `-ListenAddress` и реальный source IPv4 Linux VPN-клиента в allowlist.
LAN и VPN endpoints можно создать отдельными вызовами. Firewall rule привязана
к конкретным LocalAddress/port и remote allowlist; `Profile Any` позволяет
применять её на VPN-интерфейсе с Public profile, без открытия всем Public-клиентам.

Скрипт проверяет NAT IP именно `eth0`, доступность guest SSH, enabled default-deny
Windows firewall и отсутствие чужого listener/rule. Ownership record находится
в `%ProgramData%\nixos-wsl-base`; повтор обновляет только этот owned endpoint.
Чужие listeners/firewall rules не сбрасываются. После изменения WSL IP повторить
тот же вызов. `-WhatIf` не меняет правила, но query может запустить WSL.
Другие существующие широкие Allow rules могут дополнительно разрешать этот порт:
проверить firewall policy отдельно, особенно на управляемом компьютере.

При необходимости держать новую WSL активной без открытого терминала, явно
запустить из обычного PowerShell владельца (не создаёт scheduled task):

```powershell
Start-Process wsl.exe -WindowStyle Hidden -ArgumentList @(
  '--distribution', 'NixOS-Managed', '--user', 'swayf', '--exec', 'sleep', 'infinity'
)
```

Это не автозапуск после reboot и не доступность во время Windows sleep.
Не создавать router WAN forwarding. Не отключать Windows firewall ради SSH.

Удаление только нашего endpoint — отдельная admin-команда:

```powershell
.\scripts\expose-ssh.ps1 -Distribution NixOS-Managed `
  -ListenAddress 192.168.1.50 -ListenPort 2222 -Remove
```

## A4 — проверить SSH host key и administrative deployment

С доверенной Windows-консоли получить SERVER fingerprint:

```powershell
wsl -d NixOS-Managed -u root -- ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

На Linux-клиенте выполнить первый SSH и сравнить предложенный fingerprint с
Windows-консолью **до согласия**. Не отключать host-key checking; `ssh-keyscan`
сам по себе не устанавливает доверие. `CLIENT_KEY` — существующий приватный
ключ на Linux-клиенте, соответствующий публичному ключу в базе:

```bash
ssh -o ForwardAgent=no -o IdentitiesOnly=yes -i ~/.ssh/CLIENT_KEY \
  -p 2222 swayf@192.168.1.50
```

После проверки добавить alias в клиентский `~/.ssh/config`:

```sshconfig
Host wsl-managed
    HostName 192.168.1.50
    Port 2222
    User swayf
    IdentityFile ~/.ssh/CLIENT_KEY
    IdentitiesOnly yes
    ForwardAgent no
    StrictHostKeyChecking yes
    ServerAliveInterval 30
    ServerAliveCountMax 3
```

Проверить:

```bash
ssh wsl-managed 'id -un; sudo -n true; nix --version'
```

Не менять algorithms на SHA-1 ради RSA. Если клиент слишком стар для RSA-SHA2,
обновить OpenSSH либо добавить современный public key отдельным изменением.

## A5 — доставить age identity позже

Например, внутри WSL с отдельной флешки, не из Git:

```bash
(
  set -eu
  test ! -e "$HOME/.config/sops/age/wsl-keys.txt"
  install -d -m 700 "$HOME/.config/sops/age"
  install -m 600 /mnt/f/wsl-keys.txt "$HOME/.config/sops/age/wsl-keys.txt"
)
```

Заменить путь флешки; ключ не выводить и не помещать в repository, shared directory,
store или логи. База не зависит от его наличия. Перед secret-consuming switch
обязательно проверить, что identity открывает нужный ciphertext/MAC.

## A6 — обычный deployment разных профилей

На Linux-controller хранить PRIVATE flake с application declarations/ciphertext.
Его input `wsl-base` закрепить на опубликованный base commit и **в каждом профиле**
импортировать `inputs.wsl-base.nixosModules.default`. Пример composition:

```nix
nixosConfigurations.wsl-gui = inputs.nixpkgs.lib.nixosSystem {
  system = "x86_64-linux";
  modules = [
    inputs.wsl-base.nixosModules.default
    ./profiles/gui.nix
  ];
};
```

`gui.nix` подключает caller-pinned Home Manager/core GUI/SOPS, Podman и encrypted
source; задаёт HM `sops.age.keyFile = "/home/swayf/.config/sops/age/wsl-keys.txt"`.
Там нельзя выключать management SSH или переопределять sudo/trusted-users/user.
Private flake и GitHub credentials на target не нужны: build происходит на Linux,
затем closure копируется по SSH; секрет расшифровывается только на WSL при activation.

**Не deploy application snapshot без management module напрямую
на новую SSH-базу: так можно потерять SSH-доступ.** Для remote-managed GUI нужна
композиция с общей базой; совместимость проверяется до первого switch.

```bash
# Точный base pin либо выбранный PRIVATE application profile:
nixos-rebuild build --flake ./machines#wsl-gui
nixos-rebuild dry-activate --flake ./machines#wsl-gui \
  --target-host wsl-managed --sudo

# Только после review/preflight, это МЕНЯЕТ удалённую систему:
nixos-rebuild switch --flake ./machines#wsl-gui \
  --target-host wsl-managed --sudo
```

Для современных nixos-rebuild-ng `--sudo` — alias `--elevate=sudo`;
у старых версий есть `--use-remote-sudo`. Проверить `--help` своего инструмента.
Без `--build-host` build локальный. Для remote build можно явно передать
`--build-host wsl-managed`; это другая схема и её ресурсы/доверие проверяются отдельно.
Profile `wsl-base` в private flake должен импортировать ту же management base:
возврат к нему отключает приложения, но сохраняет удалённый доступ.

Если activation неудачна, не удалять WSL. Использовать известную рабочую base
конфигурацию или локальную Windows root-консоль для восстановления предыдущего
system generation. Windows-владелец может вызвать `wsl -u root`, поэтому эта
база не является защитной границей от Windows account owner.

## Статус проверок

Точные результаты текущей подготовки фиксируются отдельно в private workspace;
публичный README не является доказательством успешного Windows runtime.
Nix evaluation/base toplevel build и mock tests не доказывают LAN/VPN доступность.
Реальный import/switch, Windows firewall, IP refresh и remote deployment требуют
Windows-пилота. Ни публикация, ни установка, ни identity transfer не выполняются
командами проверки исходников.
