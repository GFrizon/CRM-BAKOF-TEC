Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "C:\AppMonitoramento\Monitoramento Clientes Bakof"

cmd = "cmd /k cd /d ""C:\AppMonitoramento\Monitoramento Clientes Bakof"" && " & _
      """C:\AppMonitoramento\Monitoramento Clientes Bakof\.venv\Scripts\python.exe"" " & _
      """C:\AppMonitoramento\Monitoramento Clientes Bakof\app.py"""

sh.Run cmd, 1, False
