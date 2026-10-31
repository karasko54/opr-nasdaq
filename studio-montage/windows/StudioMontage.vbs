' ============================================================================
' Studio Montage - lanceur Windows
' Demarre le petit serveur local (sans fenetre) puis ouvre le navigateur.
' Ce fichier se trouve dans %USERPROFILE%\StudioMontage et se repere tout seul.
' ============================================================================
Option Explicit
Dim sh, fso, baseDir, portFile, port, i, ts, existing

Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
portFile = baseDir & "\.port"

' --- Si le studio tourne deja, on ouvre juste l'onglet ---
existing = ReadPort()
If existing <> "" Then
  If IsUp(existing) Then
    sh.Run "http://127.0.0.1:" & existing & "/", 1, False
    WScript.Quit
  End If
End If

' --- Sinon on (re)demarre proprement ---
On Error Resume Next
If fso.FileExists(portFile) Then fso.DeleteFile portFile
On Error Goto 0

sh.CurrentDirectory = baseDir
' 0 = fenetre cachee, False = ne pas attendre la fin
sh.Run "cmd /c node """ & baseDir & "\app\server.js""", 0, False

port = ""
For i = 1 To 80          ' ~20 s d'attente max
  port = ReadPort()
  If port <> "" Then
    If IsUp(port) Then Exit For
  End If
  WScript.Sleep 250
Next

If port = "" Then port = "4178"
sh.Run "http://127.0.0.1:" & port & "/", 1, False

' ----------------------------------------------------------------------------
Function ReadPort()
  ReadPort = ""
  On Error Resume Next
  If fso.FileExists(portFile) Then
    Dim t
    Set t = fso.OpenTextFile(portFile, 1)
    ReadPort = Trim(t.ReadAll)
    t.Close
  End If
  On Error Goto 0
End Function

Function IsUp(p)
  IsUp = False
  On Error Resume Next
  Dim http
  Set http = CreateObject("MSXML2.XMLHTTP")
  http.Open "GET", "http://127.0.0.1:" & p & "/api/ping", False
  http.Send
  If Err.Number = 0 Then
    If http.Status = 200 Then IsUp = True
  End If
  On Error Goto 0
End Function
