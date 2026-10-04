Set filesystem = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
folder = filesystem.GetParentFolderName(WScript.ScriptFullName)
desktop = folder & "\Reassembly Filters.exe"
python = folder & "\.venv\Scripts\pythonw.exe"
If filesystem.FileExists(desktop) Then
  shell.Run Chr(34) & desktop & Chr(34), 1, False
ElseIf filesystem.FileExists(python) Then
  shell.Run Chr(34) & python & Chr(34) & " " & Chr(34) & folder & "\launcher.py" & Chr(34), 1, False
Else
  MsgBox "Run Setup.cmd first, or download the packaged desktop release.", 64, "Reassembly Filters"
End If
