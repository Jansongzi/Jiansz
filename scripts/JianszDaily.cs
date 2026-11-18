using System;
using System.Diagnostics;
using System.IO;
using System.IO.Pipes;
using System.Reflection;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Text.RegularExpressions;
using System.Xml;

[assembly: AssemblyTitle("简小松日常启动器")]
[assembly: AssemblyProduct("简小松")]

internal static class JianszDaily
{
    private const string TaskName = @"\Jiansz_AutoRun";

    private static int Main(string[] args)
    {
        Console.OutputEncoding = new UTF8Encoding(false);
        bool statusOnly = args.Length == 1 && args[0] == "--status";
        if (!statusOnly && args.Length != 0)
        {
            Console.WriteLine("{\"status\":\"invalid\",\"message\":\"用法：Jiansz-Daily.exe [--status]\"}");
            return 2;
        }

        try
        {
            string directory = Path.GetDirectoryName(typeof(JianszDaily).Assembly.Location);
            string name = GetPipeName(directory);
            string request = statusOnly ? "status\n" : "start\n";
            string reply = Send(name, request, 1200);
            if (reply == null && !statusOnly)
            {
                StartAssistant(directory);
                // The scheduled action already passes --start. A second start request could
                // begin another round if the first finishes before this client reconnects.
                reply = Send(name, "status\n", 20000);
                if (reply == null)
                    throw new IOException("已请求启动简小松，但尚未连接到运行实例。请查看窗口或用 --status 查询，避免重复启动。");
            }
            Console.WriteLine(reply ?? "{\"status\":\"offline\",\"message\":\"未连接到简小松。\"}");
            return reply != null && Regex.IsMatch(reply, "\"status\"\\s*:\\s*\"error\"") ? 1 : 0;
        }
        catch (Exception error)
        {
            string message = Regex.Replace(error.Message, "[\\\\\"\\x00-\\x1f]",
                match => "\\u" + ((int)match.Value[0]).ToString("x4"));
            Console.WriteLine("{\"status\":\"error\",\"message\":\"" + message + "\"}");
            return 1;
        }
    }

    private static void StartAssistant(string directory)
    {
        bool validTask = false;
        try
        {
            ValidateScheduledTask(directory);
            validTask = true;
        }
        catch (IOException) { }
        catch (XmlException) { }
        if (validTask)
        {
            RunTaskScheduler("/Run /TN \"" + TaskName + "\"");
            return;
        }

        string executable = Path.Combine(directory, "Jiansz.exe");
        if (!File.Exists(executable))
            throw new FileNotFoundException("同一目录下未找到 Jiansz.exe，请完整解压后重试。");
        using (Process.Start(new ProcessStartInfo
        {
            FileName = executable,
            Arguments = "--start",
            WorkingDirectory = directory,
            UseShellExecute = true,
            Verb = "runas",
        })) { }
    }

    private static void ValidateScheduledTask(string directory)
    {
        var task = new XmlDocument { XmlResolver = null };
        task.LoadXml(RunTaskScheduler("/Query /XML /TN \"" + TaskName + "\""));
        var namespaces = new XmlNamespaceManager(task.NameTable);
        namespaces.AddNamespace("t", "http://schemas.microsoft.com/windows/2004/02/mit/task");
        XmlNode action = task.SelectSingleNode("/t:Task/t:Actions/t:Exec", namespaces);
        XmlNodeList actions = task.SelectNodes("/t:Task/t:Actions/*", namespaces);
        XmlNode command = action == null ? null : action.SelectSingleNode("t:Command", namespaces);
        XmlNode argument = action == null ? null : action.SelectSingleNode("t:Arguments", namespaces);
        if (actions.Count != 1 || command == null || argument == null ||
            !string.Equals(command.InnerText.Trim().Trim('"'), Path.Combine(directory, "Jiansz.exe"),
                StringComparison.OrdinalIgnoreCase) ||
            (argument.InnerText.Trim() != "--start" && argument.InnerText.Trim() != "--start-daily"))
            throw new IOException("Jiansz_AutoRun 的启动命令与当前安装不符。");
    }

    private static string RunTaskScheduler(string arguments)
    {
        using (var process = Process.Start(new ProcessStartInfo
        {
            FileName = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "schtasks.exe"),
            Arguments = arguments,
            UseShellExecute = false,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true,
        }))
        {
            var output = process.StandardOutput.ReadToEndAsync();
            var error = process.StandardError.ReadToEndAsync();
            if (!process.WaitForExit(6000))
            {
                process.Kill();
                throw new IOException("Windows 计划任务服务响应超时。");
            }
            if (process.ExitCode != 0)
                throw new IOException("无法调用计划任务 Jiansz_AutoRun。 " + error.Result.Trim());
            return output.Result.TrimStart('\uFEFF', '\r', '\n');
        }
    }

    private static string GetPipeName(string directory)
    {
        string digest;
        using (var sha = SHA256.Create())
        {
            byte[] hash = sha.ComputeHash(Encoding.UTF8.GetBytes(directory.ToLowerInvariant()));
            digest = BitConverter.ToString(hash, 0, 12).Replace("-", "").ToLowerInvariant();
        }
        using (var identity = WindowsIdentity.GetCurrent())
        using (var process = Process.GetCurrentProcess())
            return string.Format("Jiansz-{0}-{1}-{2}", identity.User.Value, process.SessionId, digest);
    }

    private static string Send(string name, string request, int timeout)
    {
        using (var pipe = new NamedPipeClientStream(".", name, PipeDirection.InOut, PipeOptions.Asynchronous))
        {
            try { pipe.Connect(timeout); }
            catch (TimeoutException) { return null; }

            byte[] bytes = Encoding.ASCII.GetBytes(request);
            pipe.Write(bytes, 0, bytes.Length);
            using (var reader = new StreamReader(pipe, Encoding.UTF8))
            {
                var response = reader.ReadLineAsync();
                if (!response.Wait(5000) || string.IsNullOrEmpty(response.Result))
                    throw new IOException("简小松未确认本次请求。请查看界面或用 --status 查询状态。");
                return response.Result;
            }
        }
    }
}
