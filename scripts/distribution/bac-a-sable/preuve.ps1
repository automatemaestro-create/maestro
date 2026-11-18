# Preuve d'une installation reelle de Maestro sur une machine vierge, le Bac a sable Windows
# (#645, docs/54 section 3.5). Pose par scripts/distribution/bac-a-sable.sh.
#
# Joue au demarrage du bac a sable (LogonCommand du .wsb), sous le compte du bac a sable.
# Il ne fait que ce que ferait la personne qui installe, et il RELEVE : il n'appuie sur aucun
# bouton de Maestro. Les gestes de la personne (connexion au compte, run, "Redemarrer
# maintenant", "Reprendre") sont dans A-VOUS.txt, qu'il ouvre.
#
# Sortie : C:\preuve\releve-<date>-<heure>\ (le dossier partage avec le poste) :
#   rapport.txt      une ligne par constat
#   chronologie.tsv  toutes les 10 s : version servie, runs (id, statut, cause), executables
#   coque.log        la sortie de la premiere fenetre de Maestro (la relance par l'Explorateur
#                    apres "Redemarrer maintenant" n'a pas de sortie a capter)
# Un releve par passage : un passage precedent n'est jamais recouvert.
# La releve s'arrete quand C:\preuve\fin existe, ou au bout de 3 heures.
#
# ASCII pur : PowerShell 5.1 lit un script sans BOM dans la page de code du poste.

$ErrorActionPreference = 'Continue'
$Racine = 'C:\preuve'
$Releve = Join-Path $Racine ('releve-{0:yyyyMMdd-HHmmss}' -f (Get-Date))
New-Item -ItemType Directory -Force $Releve | Out-Null
# Le marqueur de fin d'un passage precedent arreterait celui-ci avant qu'il commence.
Remove-Item (Join-Path $Racine 'fin') -ErrorAction SilentlyContinue
$Rapport = Join-Path $Releve 'rapport.txt'
$Chrono = Join-Path $Releve 'chronologie.tsv'

function Constat([string]$texte) {
    $ligne = '{0:yyyy-MM-dd HH:mm:ss}  {1}' -f (Get-Date), $texte
    Add-Content -Path $Rapport -Value $ligne -Encoding UTF8
}

# --- 1. le poste est vierge ------------------------------------------------------------------
$os = Get-CimInstance Win32_OperatingSystem
Constat ("poste : {0} {1}, compte {2}" -f $os.Caption, $os.Version, $env:USERNAME)
foreach ($outil in 'python', 'python3', 'py', 'node', 'npm', 'git', 'claude', 'docker') {
    $trouve = Get-Command $outil -ErrorAction SilentlyContinue
    if ($trouve) { Constat ("poste : {0} PRESENT ({1})" -f $outil, $trouve.Source) }
    else { Constat ("poste : {0} absent du PATH" -f $outil) }
}
foreach ($dossier in 'C:\Program Files\Git', 'C:\Program Files\nodejs', "$env:LOCALAPPDATA\Programs\Python",
    'C:\Program Files\Python313', "$env:USERPROFILE\.maestro", "$env:USERPROFILE\.claude") {
    Constat ("poste : {0} {1}" -f $dossier, $(if (Test-Path $dossier) { 'EXISTE' } else { 'absent' }))
}

# --- 2. installer, en silence, comme le ferait un double-clic -----------------------------------
$installeur = Get-ChildItem $Racine -Filter 'Maestro-*-installation-x64.exe' | Select-Object -First 1
if (-not $installeur) { Constat 'installation : aucun installeur sous C:\preuve'; exit 1 }
Constat ("installation : {0} ({1} octets)" -f $installeur.Name, $installeur.Length)
$debut = Get-Date
$p = Start-Process $installeur.FullName -ArgumentList '/S' -Wait -PassThru
$Install = Join-Path $env:LOCALAPPDATA 'Programs\Maestro'
Constat ("installation : code {0} en {1:N0} s ; Maestro.exe {2}" -f $p.ExitCode,
    ((Get-Date) - $debut).TotalSeconds, $(if (Test-Path "$Install\Maestro.exe") { 'pose' } else { 'ABSENT' }))
$nb = (Get-ChildItem $Install -Recurse -File -ErrorAction SilentlyContinue | Measure-Object).Count
Constat ("installation : {0} fichiers sous {1}" -f $nb, $Install)

# --- 3. ouvrir Maestro ---------------------------------------------------------------------------
Start-Process (Join-Path $Install 'Maestro.exe') -WorkingDirectory $Install `
    -RedirectStandardOutput (Join-Path $Releve 'coque.log') -RedirectStandardError (Join-Path $Releve 'coque.err')
Constat 'ouverture : Maestro.exe lance'

$Connexion = Join-Path $Install 'resources\produit\runtime\python\maestro-connexion.exe'
@"
Preuve d'une installation reelle - ce qui vous revient, dans ce bac a sable
==========================================================================

Le releve tourne seul ($Releve). Vos gestes, dans l'ordre :

1. Connectez Maestro a votre compte Claude. L'ecran du modele, au premier lancement, dit
   comment. Si l'ecran vous demande une commande, la voici :
     "$Connexion"
   (double-cliquez-la dans l'Explorateur, ou collez-la dans une invite de commandes).

2. Creez un projet et lancez un run dont le travail dure plusieurs minutes.

3. Quand Maestro annonce "Maestro <version> est prete a s'installer", PENDANT que le run
   travaille, pressez "Redemarrer maintenant". Si l'annonce est arrivee avant le run, pressez
   "Plus tard", lancez le run, puis FERMEZ la fenetre de Maestro pendant qu'il travaille :
   la version se pose a la fermeture. Rouvrez alors Maestro par son raccourci.

4. Une fois Maestro rouvert sur la nouvelle version, ouvrez le run : il doit dire qu'il a ete
   arrete avec Maestro. Pressez "Reprendre", et laissez-le finir.

5. Quand le run est fini, creez un fichier vide C:\preuve\fin (ou laissez le releve
   s'arreter seul au bout de 3 heures). Vous pouvez ensuite fermer le bac a sable : il
   s'efface, le releve reste sur le poste.
"@ | Set-Content -Path (Join-Path $Racine 'A-VOUS.txt') -Encoding UTF8
Start-Process notepad.exe (Join-Path $Racine 'A-VOUS.txt')

# --- 4. la releve --------------------------------------------------------------------------------
$Session = Join-Path $env:USERPROFILE '.maestro\lanceur\session.json'
$JetonFichier = Join-Path $env:USERPROFILE '.maestro\jeton-api'
Set-Content -Path $Chrono -Value "heure`tversion`truns`texecutables" -Encoding UTF8

function Appel([string]$chemin) {
    try {
        $s = Get-Content $Session -Raw -ErrorAction Stop | ConvertFrom-Json
        $api = $s.services | Where-Object { $_.nom -eq 'api' } | Select-Object -First 1
        $jeton = (Get-Content $JetonFichier -Raw -ErrorAction Stop).Trim()
        $url = "http://127.0.0.1:{0}{1}" -f $api.port, $chemin
        return Invoke-RestMethod -Uri $url -Headers @{ Authorization = "Bearer $jeton" } -TimeoutSec 5
    } catch { return $null }
}

$dejaVues = @{}
$fin = (Get-Date).AddHours(3)
while ((Get-Date) -lt $fin -and -not (Test-Path (Join-Path $Racine 'fin'))) {
    $inst = Appel '/api/installation'
    $version = if ($inst) { [string]$inst.version } else { '-' }
    $runs = Appel '/api/executions?projet=tous'
    $texteRuns = if ($runs) {
        ($runs | ForEach-Object { '{0}:{1}:{2}' -f $_.run_id, $_.statut, $_.cause }) -join ' '
    } else { '-' }
    $exes = Get-CimInstance Win32_Process |
        Where-Object { $_.ExecutablePath -and $_.Name -match '^(Maestro|python|node|claude)' } |
        ForEach-Object { '{0}={1}' -f $_.Name, $_.ExecutablePath } | Sort-Object -Unique
    Add-Content -Path $Chrono -Value ("{0:HH:mm:ss}`t{1}`t{2}`t{3}" -f (Get-Date), $version, $texteRuns, ($exes -join ' | ')) -Encoding UTF8
    foreach ($e in $exes) {
        if (-not $dejaVues.ContainsKey($e)) {
            $dejaVues[$e] = $true
            $dehors = -not $e.Contains($Install)
            Constat ("qui sert : {0}{1}" -f $e, $(if ($dehors) { '   <- HORS DE L INSTALLATION' } else { '' }))
        }
    }
    if ($version -ne '-' -and -not $dejaVues.ContainsKey("version $version")) {
        $dejaVues["version $version"] = $true
        Constat ("version servie : {0} (cli : {1})" -f $version, ($inst | ConvertTo-Json -Compress -Depth 4))
    }
    Start-Sleep -Seconds 10
}
Constat 'releve terminee'
Get-ChildItem (Join-Path $env:USERPROFILE '.maestro\lanceur') -Filter '*.log' -ErrorAction SilentlyContinue |
    ForEach-Object { Copy-Item $_.FullName (Join-Path $Releve ("lanceur-" + $_.Name)) }
Copy-Item $Session (Join-Path $Releve 'session.json') -ErrorAction SilentlyContinue
$runs = Appel '/api/executions?projet=tous'
if ($runs) { $runs | ConvertTo-Json -Depth 6 | Set-Content (Join-Path $Releve 'runs-fin.json') -Encoding UTF8 }
