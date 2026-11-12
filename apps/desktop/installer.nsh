; La reprise d'une installation anterieure (#1620). Fichier en ASCII pur : makensis le lit
; dans le jeu de caracteres du poste quand rien ne lui dit le contraire.
;
; Jusqu'a 0.1.0, le produit installe ecrivait sa configuration sous resources\produit\core\,
; dans le dossier qu'une mise a jour efface : l'installeur joue l'ancien desinstalleur, qui
; retire tout le dossier, avant de poser le nouveau paquet. Ce crochet passe avant lui : il
; recopie l'ancien core\ et l'ancien .env sous ~\.maestro\reprise\, et le lanceur range cette
; copie au premier demarrage (maestro/lanceur/reprise.py, qui decide de ce qui est une donnee).
;
; customInit se joue dans .onInit, une fois $INSTDIR lu dans le registre (ou pris de /D=) :
; c'est le dossier de l'installation en place, celui que uninstallOldVersion efface juste apres.
;
; Le dossier personnel est celui de USERPROFILE, comme pour le produit (Path.home() en Python,
; os.homedir() dans la coque) ; $PROFILE, que Windows tient du compte, n'est qu'un repli.
;
; Rien ne se recopie quand la reprise est deja faite (son temoin, reprise.json) ou qu'une copie
; attend deja d'etre rangee : une seconde copie ecraserait la premiere avec les fichiers d'un
; paquet plus recent.

!macro customInit
  Push $0
  ReadEnvStr $0 USERPROFILE
  StrCmp $0 "" 0 +2
    StrCpy $0 $PROFILE
  IfFileExists "$0\.maestro\reprise\reprise.json" maestro_reprise_fin
  IfFileExists "$0\.maestro\reprise\core\*.*" maestro_reprise_fin
  IfFileExists "$INSTDIR\resources\produit\core\*.*" 0 maestro_reprise_fin
    CreateDirectory "$0\.maestro\reprise\core"
    CopyFiles /SILENT "$INSTDIR\resources\produit\core\*.*" "$0\.maestro\reprise\core"
    IfFileExists "$INSTDIR\resources\produit\.env" 0 maestro_reprise_fin
      CopyFiles /SILENT "$INSTDIR\resources\produit\.env" "$0\.maestro\reprise\.env"
  maestro_reprise_fin:
  Pop $0
!macroend

; Le cache des mises a jour (#644) : electron-updater y range l'installeur qu'il a telecharge,
; sous %LOCALAPPDATA%\<nom>-updater, ou <nom> est le nom du paquet empaquete
; (build.extraMetadata.name de package.json, "maestro"). Il part avec une desinstallation, et
; jamais pendant une mise a jour : l'installeur qui joue l'ancien desinstalleur (--updated) est
; celui-la meme qui vit dans ce dossier.

!macro customUnInstall
  ${ifNot} ${isUpdated}
    RMDir /r "$LOCALAPPDATA\maestro-updater"
  ${endIf}
!macroend
