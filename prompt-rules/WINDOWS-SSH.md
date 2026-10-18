# WINDOWS-SSH：Windows 目标机的 SSH 登录流程

本规则由 WINDOWS-TOOLS.md 加载，适用于通过 SSH 访问用户授权的 Windows 目标机。

## 会话准备

1. 使用 PowerShell 结束已有的 ssh、wt、WindowsTerminal 和 OpenConsole 进程。
2. 使用 Windows 自带的 OpenSSH：

   ~~~text
   C:\Windows\System32\OpenSSH\ssh.exe
   ~~~

3. 使用 Codex 工具目录中的 AskPass 脚本：

   ~~~powershell
   $toolsDir = Join-Path $env:USERPROFILE '.codex\tools'
   $askpassScript = Join-Path $toolsDir 'Invoke-SshAskPass.ps1'
   $env:SSH_ASKPASS = 'powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "' + $askpassScript + '"'
   $env:SSH_ASKPASS_REQUIRE = 'force'
   $env:DISPLAY = 'codex'
   ~~~

   Invoke-SshAskPass.ps1 使用 Windows 原生密码框接收用户手动输入。

## 建立持久 SSH 会话

1. 使用 Codex 的 PTY 会话启动 Windows 自带 ssh.exe，并保留返回的 session_id：

   ~~~powershell
   $sshArgs = @(
       '-tt',
       '-o', 'BatchMode=no',
       '-o', 'PreferredAuthentications=password',
       '-o', 'PubkeyAuthentication=no',
       '-o', 'GSSAPIAuthentication=no',
       '-o', 'ConnectTimeout=10',
       '-o', 'StrictHostKeyChecking=yes',
       '-p', '<TargetPort>',
       '<TargetUser>@<TargetAddress>',
       'powershell.exe -NoLogo -NoProfile'
   )
   & 'C:\Windows\System32\OpenSSH\ssh.exe' @sshArgs
   ~~~

2. 用户在 Windows 原生 SSH password 框中手动输入密码。
3. 读取同一个 session_id 的输出，确认远端出现 PowerShell 提示符。
4. 通过同一个 session_id 发送已授权的 PowerShell 命令，每条命令以 CRLF 结束；命令完成后读取输出再发送下一条。

   ~~~text
   hostname
   <AuthorizedCommand>
   ~~~

5. 所有命令完成后，通过同一个 session_id 发送 exit，读取 SSH 退出状态。
