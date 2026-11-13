# Installs Stenny on a Windows PC, or updates it, with one line in PowerShell and no questions:
#   irm https://github.com/hendrikboettcher/stenny/releases/latest/download/install.ps1 | iex
#
# 1. checks the PC (64-bit Intel/AMD, Windows 10 1809+, 5 GB free) and that nothing is being recorded
# 2. downloads the newest release's code into %LOCALAPPDATA%\Programs\stenny (an older one goes to the Recycle Bin)
# 3. installs uv and Stenny's engine (`uv tool install -e`), as SETUP.md does
# 4. runs the release's setup program silently (for this user only, into %LOCALAPPDATA%\Stenny, no administrator)
# 5. first time only: the Stenny folder (Documents\Stenny); every time: the task that finishes final transcripts
# 6. starts Stenny, then downloads Whisper (1.6 GB) with progress. The app's setup list does the rest
#    (courses, Claude, calendar).
# Downloads with PowerShell, not a browser, so Windows doesn't stop the unsigned setup program ("Windows protected
# your PC"). Speaks German when Windows does.
#
# For testing: $env:STENNY_TAG = "v0.5.0" installs that release instead of the newest.
# $env:STENNY_FORCE = "1" replaces an engine that runs from a git checkout (a developer's), which it otherwise leaves alone.

# One script block that runs on the last line, so a download cut off halfway runs nothing.
& {
    # uv and Stenny's engine print UTF-8, but Windows PowerShell reads what a program prints in the console's old code
    # page (850 in Germany): a user folder with a letter like o-umlaut came back garbled, so stenny.exe "didn't exist"
    # and the script stopped after installing the app. UTF-8 while it runs, then back as it was: irm | iex runs it in
    # the user's own PowerShell window.
    $savedEncoding = [Console]::OutputEncoding
    $savedPythonUtf8 = $env:PYTHONUTF8
    try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch {}
    $env:PYTHONUTF8 = '1'   # the engine's output in UTF-8 too: Python would use the ANSI code page
    try { & {
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest's progress bar makes big downloads very slow
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

    $Repo = 'hendrikboettcher/stenny'
    $Folder = Join-Path $env:LOCALAPPDATA 'Programs\stenny'
    $de = (Get-UICulture).TwoLetterISOLanguageName -eq 'de'
    function Say($en, $deText) { if ($de) { Write-Host $deText } else { Write-Host $en } }
    function Step($en, $deText) { Write-Host ''; if ($de) { Write-Host $deText -ForegroundColor Cyan } else { Write-Host $en -ForegroundColor Cyan } }
    function Fail($en, $deText) { Write-Host ''; if ($de) { Write-Host "X $deText" -ForegroundColor Red } else { Write-Host "X $en" -ForegroundColor Red } }

    # 1. The PC
    Step 'Checking this PC...' 'Pruefe den PC...'
    # Windows' own value (AMD64, ARM64 or x86), also right in an emulated or 32-bit PowerShell. Not .NET's
    # RuntimeInformation.OSArchitecture: Windows PowerShell 5.1, the one `irm | iex` runs in, returns nothing for it.
    $arch = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment' -ErrorAction SilentlyContinue).PROCESSOR_ARCHITECTURE
    if (-not $arch) { $arch = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE } }
    if ("$arch" -ne 'AMD64') {
        Fail "Stenny needs a 64-bit Intel or AMD processor; this PC has $arch." "Stenny braucht einen 64-Bit-Prozessor von Intel oder AMD; dieser PC hat $arch."
        return
    }
    $build = [Environment]::OSVersion.Version.Build
    if ($build -lt 17763) {
        Fail 'Stenny needs Windows 10 (version 1809) or newer: Settings > Windows Update.' 'Stenny braucht Windows 10 (Version 1809) oder neuer: Einstellungen > Windows Update.'
        return
    }
    $freeGb = [math]::Floor((Get-PSDrive ($env:LOCALAPPDATA.Substring(0, 1))).Free / 1GB)
    if ($freeGb -lt 5) {
        Fail "Stenny needs 5 GB of free space; this PC has $freeGb GB." "Stenny braucht 5 GB freien Speicher; dieser PC hat $freeGb GB."
        return
    }
    # A recording or a final transcript: the recorder, or the engine running `live` / `queue run`
    $procs = Get-CimInstance Win32_Process -Filter "Name = 'stenny-rec.exe' OR Name LIKE 'python%' OR Name = 'stenny.exe'"
    if ($procs | Where-Object { $_.Name -eq 'stenny-rec.exe' -or $_.CommandLine -match '(-m stenny|stenny(\.exe)?"?)\s+(live|queue run)(\s|$)' }) {
        Fail "Stenny is recording or making a final transcript. Run this again once it's done." 'Stenny nimmt gerade auf oder macht ein finales Transkript. Starte das hier noch mal, wenn es fertig ist.'
        return
    }

    # uv, and where the engine is installed from now
    $uv = (Get-Command uv -ErrorAction SilentlyContinue).Source
    if (-not $uv -and (Test-Path "$env:USERPROFILE\.local\bin\uv.exe")) { $uv = "$env:USERPROFILE\.local\bin\uv.exe" }
    if ($uv) {
        $receipt = Join-Path (& $uv tool dir) 'stenny\uv-receipt.toml'
        if ((Test-Path $receipt) -and ((Get-Content $receipt -Raw) -match 'editable = "([^"]*)"')) {
            $from = $Matches[1]
            if ((Test-Path (Join-Path $from '.git')) -and $env:STENNY_FORCE -ne '1') {
                Fail "Stenny's engine here runs from a git checkout ($from), so this PC is set up for development." "Stennys Engine laeuft hier aus einem Git-Checkout ($from); dieser PC ist zum Entwickeln eingerichtet."
                Say '  Leaving it alone. To replace it anyway: $env:STENNY_FORCE = "1"' '  Ich lasse ihn, wie er ist. Um ihn trotzdem zu ersetzen: $env:STENNY_FORCE = "1"'
                return
            }
        }
    }
    if (Test-Path (Join-Path $Folder '.git')) {
        Fail "$Folder is a git checkout: use git pull there." "$Folder ist ein Git-Checkout: nutze dort git pull."
        return
    }

    # 2. The release: its code and its setup program
    Step 'Downloading Stenny...' 'Lade Stenny herunter...'
    $api = "https://api.github.com/repos/$Repo/releases/" + $(if ($env:STENNY_TAG) { "tags/$env:STENNY_TAG" } else { 'latest' })
    $release = Invoke-RestMethod $api
    $tag = $release.tag_name
    $version = $tag.TrimStart('v')
    $setupAsset = $release.assets | Where-Object { $_.name -like '*-setup.exe' } | Select-Object -First 1
    if (-not $setupAsset) {
        Fail "Stenny $version has no Windows app yet. Try again in a few minutes; it's added after the release." "Stenny $version hat noch keine Windows-App. Versuch es in ein paar Minuten noch mal; sie kommt nach dem Release dazu."
        return
    }
    $parent = Split-Path $Folder
    New-Item -ItemType Directory -Force $parent | Out-Null
    $work = Join-Path $parent (".stenny-install-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory $work | Out-Null
    try {
        Invoke-WebRequest "https://github.com/$Repo/archive/refs/tags/$tag.zip" -OutFile "$work\code.zip" -UseBasicParsing
        $setup = Join-Path $work $setupAsset.name
        Invoke-WebRequest $setupAsset.browser_download_url -OutFile $setup -UseBasicParsing
        Expand-Archive "$work\code.zip" $work
        $new = Join-Path $work "stenny-$version"
        if (-not (Test-Path "$new\pyproject.toml")) {
            Fail "The download doesn't contain Stenny's code." 'Der Download enthaelt Stennys Code nicht.'
            return
        }

        # Quit Stenny: Windows won't replace the engine or the app while they run
        $running = Get-CimInstance Win32_Process | Where-Object {
            $_.ExecutablePath -and ($_.ExecutablePath -like "$env:LOCALAPPDATA\Stenny\*" -or $_.ExecutablePath -like '*\uv\tools\stenny\*') }
        if ($running) {
            Say 'Quitting Stenny for the update...' 'Beende Stenny fuer das Update...'
            $running | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
            Start-Sleep -Seconds 1
        }
        $old = $null
        if (Test-Path $Folder) { $old = Join-Path $work 'before'; Move-Item $Folder $old }
        Move-Item $new $Folder

        # 3. uv and the engine. If that fails, the old folder comes back and nothing else has changed.
        # Each part says it's starting, and nothing says "installed" before the app is: uv's own installer ends with
        # "everything's installed!", which read as if Stenny were done minutes before it was, so its output only
        # shows if it fails. The engine and the app install quietly, so their lines tell what's going on meanwhile.
        Step "Installing Stenny $version (a few minutes)..." "Installiere Stenny $version (ein paar Minuten)..."
        Say '  1/2 The engine: Python and the speech recognition libraries...' '  1/2 Die Engine: Python und die Bibliotheken fuer die Spracherkennung...'
        if (-not $uv) {
            $env:UV_NO_MODIFY_PATH = '1'; $env:INSTALLER_NO_MODIFY_PATH = '1'   # %USERPROFILE%\.local\bin, PATH unchanged
            $uvOutput = powershell -NoProfile -ExecutionPolicy Bypass -Command 'irm https://astral.sh/uv/install.ps1 | iex'   # stdout only: 2>&1 would stop the script at uv's first stderr line in Windows PowerShell
            $uv = "$env:USERPROFILE\.local\bin\uv.exe"
            if (-not (Test-Path $uv)) {
                $uvOutput | Write-Host
                Remove-Item $Folder -Recurse -Force
                if ($old) { Move-Item $old $Folder }
                Fail "Installing uv, which installs Python for Stenny, failed (see above). Nothing was changed." 'uv, das Python fuer Stenny installiert, liess sich nicht installieren (siehe oben). Es wurde nichts geaendert.'
                return
            }
        }
        & $uv tool install -e $Folder --force --python 3.12 --quiet
        if ($LASTEXITCODE -ne 0) {
            Remove-Item $Folder -Recurse -Force
            if ($old) { Move-Item $old $Folder; & $uv tool install -e $Folder --force --python 3.12 --quiet }
            Fail "Installing Stenny's engine failed (see above). Nothing was changed." 'Stennys Engine liess sich nicht installieren (siehe oben). Es wurde nichts geaendert.'
            return
        }
        $stenny = Join-Path (& $uv tool dir --bin) 'stenny.exe'
        # The old folder only now that the new engine works: to the Recycle Bin, so it can still be brought back
        if ($old) {
            Add-Type -AssemblyName Microsoft.VisualBasic
            [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteDirectory($old, 'OnlyErrorDialogs', 'SendToRecycleBin')
        }

        # 4. The app: the setup program, silently. Installs over an older Stenny.
        Say '  2/2 The app...' '  2/2 Die App...'
        $p = Start-Process $setup -ArgumentList '/S' -Wait -PassThru
        if ($p.ExitCode -ne 0) {
            Fail "Stenny's setup program stopped with error $($p.ExitCode)." "Stennys Setup-Programm brach mit Fehler $($p.ExitCode) ab."
            return
        }
        Say "Stenny $version is installed." "Stenny $version ist installiert."
    } finally {
        Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
    }

    # 5. First time: the Stenny folder. Every time: the task that finishes waiting final transcripts on AC power.
    if (-not ((& $stenny where --json) -match '"error": null')) { & $stenny init }
    & $stenny schedule install | Out-Null
    if ($LASTEXITCODE -ne 0) { Say "! The task for final transcripts couldn't be added." '! Die Aufgabe fuer finale Transkripte liess sich nicht anlegen.' }

    # 6. Stenny (the program its uninstall entry names), then Whisper in the foreground, so its progress shows
    $entry = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -eq 'Stenny' } | Select-Object -First 1
    $exe = if ($entry -and $entry.DisplayIcon) { $entry.DisplayIcon.Trim('"').Split(',')[0] } else { $null }
    # Says it's open only if it was opened: otherwise where to find it
    $opened = $false
    if ($exe -and (Test-Path $exe)) { Start-Process $exe; $opened = $true }
    Step 'Downloading the speech recognition (Whisper, about 1.6 GB, once)...' 'Lade die Spracherkennung herunter (Whisper, etwa 1,6 GB, nur einmal)...'
    if ($opened) {
        Say "Keep this window open until it's done; Stenny is already open." 'Lass dieses Fenster offen, bis es fertig ist; Stenny ist schon offen.'
    } else {
        Say "Keep this window open until it's done. Meanwhile you can open Stenny from the Start menu." 'Lass dieses Fenster offen, bis es fertig ist. Stenny kannst du schon im Startmenue oeffnen.'
    }
    & $stenny download-model
    if ($LASTEXITCODE -ne 0) { Say '! Whisper will download with the first transcription instead.' '! Whisper wird stattdessen beim ersten Transkript heruntergeladen.' }

    Write-Host ''
    if ($opened) {
        Say "Done. Stenny's icon is next to the clock; its window shows what's left to set up." 'Fertig. Stennys Symbol ist neben der Uhr; sein Fenster zeigt, was noch einzurichten ist.'
    } else {
        Say "Done. Open Stenny from the Start menu; its window shows what's left to set up." 'Fertig. Oeffne Stenny im Startmenue; sein Fenster zeigt, was noch einzurichten ist.'
    }
    Say 'You can close this window.' 'Du kannst dieses Fenster schliessen.'
    } } finally {
        try { [Console]::OutputEncoding = $savedEncoding } catch {}
        $env:PYTHONUTF8 = $savedPythonUtf8
    }
}
