# Installs Stenny on a Windows PC, or updates it, with one line in PowerShell and no questions:
#   irm https://github.com/hendrikboettcher/stenny/releases/latest/download/install.ps1 | iex
#
# 1. checks the PC (64-bit Intel/AMD, Windows 10 1809+, 5 GB free) and that nothing is being recorded
# 2. downloads the newest release's setup program
# 3. runs it silently (for this user only, into %LOCALAPPDATA%\Stenny, no administrator). The engine is inside the
#    app (#92): nothing else is installed. An engine an older setup installed with uv (%LOCALAPPDATA%\Programs\stenny)
#    is removed by the app at its next start (src/stenny/old_engine.py).
# 4. first time only: the Stenny folder (Documents\Stenny); every time: the task that finishes final transcripts
# 5. starts Stenny, then downloads Whisper (1.6 GB) with progress. The app's setup list does the rest
#    (courses, Claude, calendar).
# Downloads with PowerShell, not a browser, so Windows doesn't stop the unsigned setup program ("Windows protected
# your PC"). Speaks German when Windows does.
#
# For testing: $env:STENNY_TAG = "v0.5.14" installs that release instead of the newest (0.5.12 or newer: the first
# with the engine inside the app).

# One script block that runs on the last line, so a download cut off halfway runs nothing.
& {
    # Stenny's engine prints UTF-8, but Windows PowerShell reads what a program prints in the console's old code
    # page (850 in Germany): a user folder with a letter like o-umlaut came back garbled, so stenny.exe "didn't exist"
    # and the script stopped after installing the app. UTF-8 while it runs, then back as it was: irm | iex runs it in
    # the user's own PowerShell window.
    $savedEncoding = [Console]::OutputEncoding
    $savedPythonUtf8 = $env:PYTHONUTF8
    $savedNoBytecode = $env:PYTHONDONTWRITEBYTECODE
    try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false } catch {}
    $env:PYTHONUTF8 = '1'   # the engine's output in UTF-8 too: Python would use the ANSI code page
    try { & {
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'   # Invoke-WebRequest's progress bar makes big downloads very slow
    [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

    $Repo = 'hendrikboettcher/stenny'
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

    # 2. The release's setup program
    Step 'Downloading Stenny...' 'Lade Stenny herunter...'
    $api = "https://api.github.com/repos/$Repo/releases/" + $(if ($env:STENNY_TAG) { "tags/$env:STENNY_TAG" } else { 'latest' })
    $release = Invoke-RestMethod $api
    $version = $release.tag_name.TrimStart('v')
    $setupAsset = $release.assets | Where-Object { $_.name -like '*-setup.exe' } | Select-Object -First 1
    if (-not $setupAsset) {
        Fail "Stenny $version has no Windows app yet. Try again in a few minutes; it's added after the release." "Stenny $version hat noch keine Windows-App. Versuch es in ein paar Minuten noch mal; sie kommt nach dem Release dazu."
        return
    }
    $work = Join-Path $env:TEMP ("stenny-install-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory $work | Out-Null
    try {
        $setup = Join-Path $work $setupAsset.name
        Invoke-WebRequest $setupAsset.browser_download_url -OutFile $setup -UseBasicParsing

        # Quit Stenny: Windows won't replace the app (and the engine inside it) while they run
        $running = Get-CimInstance Win32_Process | Where-Object {
            $_.ExecutablePath -and $_.ExecutablePath -like "$env:LOCALAPPDATA\Stenny\*" }
        if ($running) {
            Say 'Quitting Stenny for the update...' 'Beende Stenny fuer das Update...'
            $running | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
            Start-Sleep -Seconds 1
        }

        # 3. The app, with its engine: the setup program, silently. Installs over an older Stenny.
        Step "Installing Stenny $version (a few minutes)..." "Installiere Stenny $version (ein paar Minuten)..."
        $p = Start-Process $setup -ArgumentList '/S' -Wait -PassThru
        if ($p.ExitCode -ne 0) {
            Fail "Stenny's setup program stopped with error $($p.ExitCode)." "Stennys Setup-Programm brach mit Fehler $($p.ExitCode) ab."
            return
        }
        # No "installed" here: Whisper still takes minutes, and friends closed the window at that line
    } finally {
        Remove-Item $work -Recurse -Force -ErrorAction SilentlyContinue
    }

    # Stenny.exe (the program its uninstall entry names) and the engine next to it, as the app runs it
    $entry = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -eq 'Stenny' } | Select-Object -First 1
    $exe = if ($entry -and $entry.DisplayIcon) { $entry.DisplayIcon.Trim('"').Split(',')[0] } else { Join-Path $env:LOCALAPPDATA 'Stenny\Stenny.exe' }
    $python = Join-Path (Split-Path $exe) 'engine\python.exe'
    if (-not (Test-Path $python)) {
        Fail "Stenny's engine isn't next to $exe." "Stennys Engine liegt nicht neben $exe."
        return
    }
    $env:PYTHONDONTWRITEBYTECODE = '1'   # never into the app's own files
    function Invoke-Stenny { & $python -m stenny @args }

    # 4. First time: the Stenny folder. Every time: the task that finishes waiting final transcripts on AC power.
    # Quiet: init's "Wrote config.toml... Edit it" is for the command line; the app's setup list sets the courses
    if (-not ((Invoke-Stenny where --json) -match '"error": null')) { Invoke-Stenny init | Out-Null }
    Invoke-Stenny schedule install | Out-Null
    if ($LASTEXITCODE -ne 0) { Say "! The task for final transcripts couldn't be added." '! Die Aufgabe fuer finale Transkripte liess sich nicht anlegen.' }

    # 5. Stenny, then Whisper in the foreground, so its progress shows
    # Says it's open only if it was opened: otherwise where to find it
    $opened = $false
    if ($exe -and (Test-Path $exe)) { Start-Process $exe; $opened = $true }
    Step 'Downloading the speech recognition (Whisper, about 1.6 GB, once)...' 'Lade die Spracherkennung herunter (Whisper, etwa 1,6 GB, nur einmal)...'
    if ($opened) {
        Say "Keep this window open until it's done; Stenny is already open." 'Lass dieses Fenster offen, bis es fertig ist; Stenny ist schon offen.'
    } else {
        Say "Keep this window open until it's done. Meanwhile you can open Stenny from the Start menu." 'Lass dieses Fenster offen, bis es fertig ist. Stenny kannst du schon im Startmenue oeffnen.'
    }
    Invoke-Stenny download-model
    if ($LASTEXITCODE -ne 0) { Say '! Whisper will download with the first transcription instead.' '! Whisper wird stattdessen beim ersten Transkript heruntergeladen.' }

    # Where to go on, by a place everyone knows: the Start menu also brings back the window of a running Stenny
    # (single instance). Not the tray icon, "next to the clock": Windows 11 often hides it behind the ^ arrow.
    Write-Host ''
    Say "Stenny $version is installed." "Stenny $version ist installiert."
    if ($opened) {
        Say "Carry on in Stenny's window: it shows what's left to set up. If it's closed: Start menu > Stenny." 'Mach im Stenny-Fenster weiter: Es zeigt, was noch einzurichten ist. Falls es zu ist: Startmenue > Stenny.'
    } else {
        Say "Open Stenny from the Start menu; its window shows what's left to set up." 'Oeffne Stenny ueber das Startmenue; sein Fenster zeigt, was noch einzurichten ist.'
    }
    Say 'You can close this window now.' 'Du kannst dieses Fenster jetzt schliessen.'
    } } finally {
        try { [Console]::OutputEncoding = $savedEncoding } catch {}
        $env:PYTHONUTF8 = $savedPythonUtf8
        $env:PYTHONDONTWRITEBYTECODE = $savedNoBytecode
    }
}
