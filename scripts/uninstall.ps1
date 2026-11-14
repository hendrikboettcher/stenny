# Removes Stenny from a Windows PC with one line in PowerShell:
#   irm https://github.com/hendrikboettcher/stenny/releases/latest/download/uninstall.ps1 | iex
#
# 1. stops if Stenny is recording or making a final transcript, or runs from a git checkout (a developer's PC)
# 2. quits Stenny
# 3. `stenny uninstall --yes --json` (src/stenny/uninstall.py): the task for final transcripts, the connector in
#    Claude Code, the saved API key and calendar links, logs, cache and the Whisper model
# 4. the app (its own uninstaller, silently), opening at login and the app's own web data
# 5. the engine (`uv tool uninstall stenny`) and its code (%LOCALAPPDATA%\Programs\stenny)
# 6. Stenny's settings and data, with the recordings' audio, to the Recycle Bin: they can still be brought back
# The transcripts (the Stenny folder) stay: they're the student's. uv stays too: other programs may use it.
# No administrator needed. Speaks German when Windows does. ASCII only, like install.ps1 (irm | iex in PowerShell 5.1).

# One script block that runs on the last line, so a download cut off halfway runs nothing.
& {
    # uv and Stenny's engine print UTF-8; Windows PowerShell would read it in the console's old code page (see
    # install.ps1). UTF-8 while this runs, then back as it was.
    $savedEncoding = [Console]::OutputEncoding
    $savedPythonUtf8 = $env:PYTHONUTF8
    try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch {}
    $env:PYTHONUTF8 = '1'
    try { & {
    $ErrorActionPreference = 'Stop'

    $Folder = Join-Path $env:LOCALAPPDATA 'Programs\stenny'
    $de = (Get-UICulture).TwoLetterISOLanguageName -eq 'de'
    function Say($en, $deText) { if ($de) { Write-Host $deText } else { Write-Host $en } }
    function Step($en, $deText) { Write-Host ''; if ($de) { Write-Host $deText -ForegroundColor Cyan } else { Write-Host $en -ForegroundColor Cyan } }
    function Fail($en, $deText) { Write-Host ''; if ($de) { Write-Host "X $deText" -ForegroundColor Red } else { Write-Host "X $en" -ForegroundColor Red } }
    Add-Type -AssemblyName Microsoft.VisualBasic
    function ToRecycleBin($path) {
        if (-not (Test-Path $path)) { return $true }
        try { [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory($path, 'OnlyErrorDialogs', 'SendToRecycleBin'); return $true }
        catch { return $false }
    }

    # 1. Nothing running that mustn't be stopped, and not a developer's PC
    Step 'Removing Stenny...' 'Entferne Stenny...'
    $procs = Get-CimInstance Win32_Process -Filter "Name = 'stenny-rec.exe' OR Name LIKE 'python%' OR Name = 'stenny.exe'"
    if ($procs | Where-Object { $_.Name -eq 'stenny-rec.exe' -or $_.CommandLine -match '(-m stenny|stenny(\.exe)?"?)\s+(live|queue run)(\s|$)' }) {
        Fail "Stenny is recording or making a final transcript. Run this again once it's done." 'Stenny nimmt gerade auf oder macht ein finales Transkript. Starte das hier noch mal, wenn es fertig ist.'
        return
    }
    $uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
    if (-not $uv -and (Test-Path "$env:USERPROFILE\.local\bin\uv.exe")) { $uv = "$env:USERPROFILE\.local\bin\uv.exe" }
    if ($uv) {
        $receipt = Join-Path (& $uv tool dir) 'stenny\uv-receipt.toml'
        if ((Test-Path $receipt) -and ((Get-Content $receipt -Raw) -match 'editable = "([^"]*)"')) {
            $from = $Matches[1]
            if ((Test-Path (Join-Path $from '.git')) -and $env:STENNY_FORCE -ne '1') {
                Fail "Stenny's engine here runs from a git checkout ($from), so this PC is set up for development." "Stennys Engine laeuft hier aus einem Git-Checkout ($from); dieser PC ist zum Entwickeln eingerichtet."
                Say '  Leaving it alone. To remove it anyway: $env:STENNY_FORCE = "1"' '  Ich lasse ihn, wie er ist. Um ihn trotzdem zu entfernen: $env:STENNY_FORCE = "1"'
                return
            }
        }
    }

    # From here on each part goes as far as it can: one that fails mustn't keep the others from running. And in
    # Windows PowerShell a native program's stderr redirected under 'Stop' would end the script at its first line.
    $ErrorActionPreference = 'Continue'

    # 2. Quit Stenny: the app and its engine
    Get-CimInstance Win32_Process | Where-Object {
        $_.ExecutablePath -and ($_.ExecutablePath -like "$env:LOCALAPPDATA\Stenny\*" -or $_.ExecutablePath -like '*\uv\tools\stenny\*') } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 1

    # 3. What the engine set up. Without a working engine (or one older than `uninstall`): the usual places.
    $stenny = $null
    if ($uv) { $stenny = Join-Path (& $uv tool dir --bin) 'stenny.exe' }
    if (-not ($stenny -and (Test-Path $stenny))) { $stenny = "$env:USERPROFILE\.local\bin\stenny.exe" }
    $result = $null
    if (Test-Path $stenny) {
        try { $result = (& $stenny uninstall --yes --json) -join "`n" | ConvertFrom-Json } catch { $result = $null }
    }
    if ($result) {
        $trash = @($result.trash)
    } else {
        schtasks /Delete /TN 'Stenny Queue' /F 2>&1 | Out-Null
        foreach ($d in "$env:LOCALAPPDATA\Stenny\Logs", "$env:LOCALAPPDATA\Stenny\Cache", "$env:LOCALAPPDATA\Stenny\Models") {
            Remove-Item $d -Recurse -Force -ErrorAction SilentlyContinue
        }
        $trash = @("$env:APPDATA\Stenny", "$env:LOCALAPPDATA\Stenny")
    }

    # 4. The app, with its own uninstaller; opening at login; the app's web data (WebView2)
    $entry = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -eq 'Stenny' } | Select-Object -First 1
    if ($entry -and $entry.UninstallString) {
        $p = Start-Process $entry.UninstallString.Trim('"') -ArgumentList '/S' -Wait -PassThru
        if ($p.ExitCode -ne 0) { Say "! Stenny's uninstaller stopped with error $($p.ExitCode)." "! Stennys Deinstallationsprogramm brach mit Fehler $($p.ExitCode) ab." }
    }
    foreach ($key in 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run', 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run') {
        Remove-ItemProperty -Path $key -Name 'Stenny' -ErrorAction SilentlyContinue
    }
    Remove-Item "$env:LOCALAPPDATA\eu.stenny.Stenny" -Recurse -Force -ErrorAction SilentlyContinue

    # 5. The engine and its code
    if ($uv) { & $uv tool uninstall stenny 2>&1 | Out-Null }
    if (-not (Test-Path (Join-Path $Folder '.git'))) { Remove-Item $Folder -Recurse -Force -ErrorAction SilentlyContinue }

    # 6. Settings and the recordings' audio, to the Recycle Bin
    foreach ($place in $trash) {
        if (-not (ToRecycleBin $place)) { Say "! $place couldn't be moved to the Recycle Bin." "! $place liess sich nicht in den Papierkorb legen." }
    }

    Write-Host ''
    Say "Stenny is removed. Its settings and the recordings' audio are in the Recycle Bin, until you empty it." 'Stenny ist entfernt. Seine Einstellungen und das Audio der Aufnahmen liegen im Papierkorb, bis du ihn leerst.'
    if ($result -and $result.folder) { Say "  Your transcripts stay in $($result.folder)." "  Deine Transkripte bleiben in $($result.folder)." }
    if ($result -and $result.claude_desktop_extension) { Say '  Claude Desktop still has the Stenny extension: Settings > Extensions > Stenny > Uninstall.' '  Claude Desktop hat noch die Stenny-Erweiterung: Einstellungen > Erweiterungen > Stenny > Deinstallieren.' }
    if ($result) { foreach ($problem in @($result.problems)) { if ($problem) { Say "! $problem" "! $problem" } } }
    Say '  uv stays, as other programs may use it. To remove it too, delete uv.exe, uvx.exe and uvw.exe in %USERPROFILE%\.local\bin.' '  uv bleibt, falls andere Programme es nutzen. Um es auch zu entfernen: loesche uv.exe, uvx.exe und uvw.exe in %USERPROFILE%\.local\bin.'
    } } finally {
        try { [Console]::OutputEncoding = $savedEncoding } catch {}
        $env:PYTHONUTF8 = $savedPythonUtf8
    }
}
