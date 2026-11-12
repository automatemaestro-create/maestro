#!/usr/bin/env bash
# Le dialogue de la coque, lu puis pressé sans la souris (#644, #1647).
#
# Sourçable, sans effet à l'inclusion :
#
#   . "$RACINE/scripts/distribution/dialogue.sh"
#   presser <pid de la fenêtre> <motif du nom du bouton>     # motif PowerShell `-like`, ex. 'Plus*'
#
# Le dialogue d'Electron (`dialog.showMessageBox`) est, sous Windows, un dialogue de tâches : une
# fenêtre `#32770` sous la fenêtre de la coque. Ses boutons (`CCPushButton` pour UI Automation)
# n'exposent aucun motif d'invocation, mais ce sont de vraies fenêtres Win32 de classe `Button`, et
# un bouton qu'on clique le notifie à son parent (`WM_COMMAND`, code `BN_CLICKED`) : c'est ce que
# `presser` envoie au parent du bouton, à la place du clic. Rien ne passe par l'écran — ni curseur,
# ni premier plan, ni clic —, si bien que le geste ne dépend pas de ce qui couvre le dialogue et ne
# touche à rien de ce que la personne au poste a ouvert. `BM_CLICK` ne convient pas : un bouton ne
# s'en presse que dans un dialogue actif, ce qu'un dialogue en arrière-plan n'est pas.
#
# Écrit les textes du dialogue (`texte : …`), puis une ligne de verdict :
#   presse : <bouton>               le dialogue s'est fermé sur ce bouton (code 0) ;
#   pression sans effet : <bouton>  notifié, mais le dialogue est resté ouvert ;
#   pression refusee : <bouton>     la notification n'a pas abouti (fenêtre qui ne répond plus) ;
#   bouton sans fenetre : <bouton>  le bouton n'est pas une fenêtre `Button` avec un parent : rien
#                                   n'est pressé, et rien n'est cliqué au jugé ;
#   aucun dialogue de mise a jour   aucun bouton de ce nom dans un dialogue du processus (1 min).
# Code : 0 sur `presse`, 1 sinon — celui de PowerShell, `pipefail` ou non chez l'appelant.
# Le script PowerShell est en ASCII, et ses guillemets doubles sont des `[char]34` : il traverse la
# ligne de commande.
presser() {
  local ps='/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe'
  env MSYS2_ARG_CONV_EXCL='*' "$ps" -NoProfile -NonInteractive -Command "
    [Console]::OutputEncoding = [Text.Encoding]::UTF8
    Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
    \$dll = '[DllImport(' + [char]34 + 'user32.dll' + [char]34 + ')] public static extern '
    Add-Type -Namespace Verification -Name Dialogue -MemberDefinition (
      \$dll + 'IntPtr GetParent(IntPtr h); ' +
      \$dll + 'int GetDlgCtrlID(IntPtr h); ' +
      \$dll + 'int GetClassName(IntPtr h, System.Text.StringBuilder s, int n); ' +
      \$dll + 'IntPtr SendMessageTimeout(IntPtr h, uint m, IntPtr w, IntPtr l, uint f, uint t, out IntPtr r);')
    \$A = [System.Windows.Automation.AutomationElement]
    \$tous = [System.Windows.Automation.Condition]::TrueCondition
    \$duProcessus = New-Object System.Windows.Automation.PropertyCondition(\$A::ProcessIdProperty, $1)
    \$dialogue = New-Object System.Windows.Automation.PropertyCondition(\$A::ClassNameProperty, '#32770')
    for (\$i = 0; \$i -lt 120; \$i++) {
      foreach (\$haut in \$A::RootElement.FindAll('Children', \$duProcessus)) {
        foreach (\$d in @(\$haut) + @(\$haut.FindAll('Descendants', \$dialogue))) {
          if (\$d.Current.ClassName -ne '#32770') { continue }
          \$elements = @(\$d.FindAll('Descendants', \$tous))
          \$bouton = \$elements | Where-Object {
            \$_.Current.ClassName -eq 'CCPushButton' -and \$_.Current.Name -like '$2'
          } | Select-Object -First 1
          if (-not \$bouton) { continue }
          \$elements | ForEach-Object { \$_.Current.Name } | Where-Object { \$_ } | ForEach-Object { 'texte : ' + \$_ }
          \$nom = \$bouton.Current.Name
          \$h = [IntPtr]\$bouton.Current.NativeWindowHandle
          \$classe = New-Object System.Text.StringBuilder 64
          \$parent = [IntPtr]::Zero
          if (\$h -ne [IntPtr]::Zero) {
            [void][Verification.Dialogue]::GetClassName(\$h, \$classe, 64)
            \$parent = [Verification.Dialogue]::GetParent(\$h)
          }
          if (\$classe.ToString() -ne 'Button' -or \$parent -eq [IntPtr]::Zero) {
            'bouton sans fenetre : ' + \$nom
            exit 1
          }
          # WM_COMMAND (0x0111) au parent : wParam = MAKEWPARAM(id, BN_CLICKED = 0), lParam = le
          # bouton. SMTO_ABORTIFHUNG (2) : une fenetre qui ne repond plus rend la main.
          \$id = [Verification.Dialogue]::GetDlgCtrlID(\$h) -band 0xFFFF
          \$r = [IntPtr]::Zero
          if ([Verification.Dialogue]::SendMessageTimeout(\$parent, 0x0111, [IntPtr]\$id, \$h, 2, 5000, [ref]\$r) -eq [IntPtr]::Zero) {
            'pression refusee : ' + \$nom
            exit 1
          }
          for (\$j = 0; \$j -lt 20; \$j++) {
            Start-Sleep -Milliseconds 250
            if (@(\$haut.FindAll('Descendants', \$dialogue)).Count -eq 0) { 'presse : ' + \$nom; exit 0 }
          }
          'pression sans effet : ' + \$nom
          exit 1
        }
      }
      Start-Sleep -Milliseconds 500
    }
    'aucun dialogue de mise a jour'
    exit 1
  " | tr -d '\r'
  return "${PIPESTATUS[0]}"
}
