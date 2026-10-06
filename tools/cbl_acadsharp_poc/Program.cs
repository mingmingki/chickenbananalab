using System.Diagnostics;
using System.Globalization;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.RegularExpressions;
using ACadSharp;
using ACadSharp.Entities;
using ACadSharp.IO;
using ACadSharp.Tables;
using CSMath;

namespace CblAcadSharpPoc;

internal static class Program
{
    private const int DefaultTimeoutSeconds = 900;

    public static int Main(string[] args)
    {
        if (args.Length >= 3 && string.Equals(args[0], "--create", StringComparison.OrdinalIgnoreCase))
            return CreateNew(args[1], args[2], args.Length >= 4 ? args[3] : null);
        if (args.Length == 2 && string.Equals(args[0], "--metadata", StringComparison.OrdinalIgnoreCase))
            return WriteMetadata(args[1]);
        if ((args.Length == 3 || args.Length == 4) && string.Equals(args[0], "--dxf", StringComparison.OrdinalIgnoreCase))
            return WriteDxfFromDwg(args[1], args[2], args.Length == 4 ? args[3] : null);
        if (args.Length == 3 && string.Equals(args[0], "--dwg-from-dxf", StringComparison.OrdinalIgnoreCase))
            return WriteDwgFromDxf(args[1], args[2]);
        // "--reread-metadata <path>": also write the --metadata report of the
        // reread output, so the server does not start a process to read it again.
        string? rereadMetadataPath = null;
        var metadataFlag = Array.FindIndex(args, x => string.Equals(x, "--reread-metadata", StringComparison.OrdinalIgnoreCase));
        if (metadataFlag >= 0)
        {
            if (metadataFlag + 1 >= args.Length) return Fail("--reread-metadata needs a path");
            rereadMetadataPath = Path.GetFullPath(args[metadataFlag + 1]);
            args = args.Where((_, index) => index != metadataFlag && index != metadataFlag + 1).ToArray();
        }
        // "--restore-from <path>": the drawing as the editor opened it, for
        // objects an earlier save deleted and undo brought back ("fromOpened").
        var restoreFlag = Array.FindIndex(args, x => string.Equals(x, "--restore-from", StringComparison.OrdinalIgnoreCase));
        if (restoreFlag >= 0)
        {
            if (restoreFlag + 1 >= args.Length) return Fail("--restore-from needs a path");
            RestoreSourcePath = Path.GetFullPath(args[restoreFlag + 1]);
            args = args.Where((_, index) => index != restoreFlag && index != restoreFlag + 1).ToArray();
        }
        if (args.Length < 2 || args.Length > 5)
        {
            Console.Error.WriteLine("usage: CblAcadSharpPoc <input.dwg> <output.dwg> [AC1018|AC2004]");
            return 2;
        }

        var input = Path.GetFullPath(args[0]);
        var output = Path.GetFullPath(args[1]);
        var versionName = args.Length >= 3 ? args[2] : "AC1018";
        var opsPath = args.Length >= 4 && File.Exists(args[3]) ? Path.GetFullPath(args[3]) : null;
        var edit = args.Any(x => string.Equals(x, "edit", StringComparison.OrdinalIgnoreCase));
        if (!File.Exists(input)) return Fail($"input does not exist: {input}");
        if (string.Equals(input, output, StringComparison.OrdinalIgnoreCase)) return Fail("refusing to overwrite input");
        if (!TryParseVersion(versionName, out var version)) return Fail($"unsupported target version: {versionName}");

        Directory.CreateDirectory(Path.GetDirectoryName(output)!);
        var lockPath = output + ".lock";
        var temp = output + $".tmp.{Environment.ProcessId}.{Guid.NewGuid():N}";
        var notifications = new List<object>();
        var sourceHash = Sha256(input);
        var sw = Stopwatch.StartNew();

        try
        {
            using var lockStream = AcquireLock(lockPath, TimeSpan.FromSeconds(DefaultTimeoutSeconds));
            var (before, editReport, storedAcis, sourceCodePage) = WriteEdited(input, temp, version, opsPath, edit, notifications);
            // The edited source is gone with WriteEdited; collect it before the
            // reread so the two drawings are not in memory together.
            GC.Collect();

            if (!File.Exists(temp) || new FileInfo(temp).Length < 1024) throw new InvalidDataException("writer produced an empty or implausibly small DWG");
            var rereadNotifications = new List<object>();
            var reread = Read(temp, rereadNotifications);
            var after = Snapshot(reread);
            if (after.EntityTotal == 0 && before.EntityTotal > 0) throw new InvalidDataException("writer reread has no entities");
            if (rereadMetadataPath != null)
                File.WriteAllText(rereadMetadataPath, JsonSerializer.Serialize(BuildMetadata(reread, output, rereadNotifications), new JsonSerializerOptions { WriteIndented = true }));
            File.Move(temp, output, true);
            var report = new
            {
                input,
                output,
                targetVersion = version.ToString(),
                sourceSha256 = sourceHash,
                outputSha256 = Sha256(output),
                sourceBytes = new FileInfo(input).Length,
                outputBytes = new FileInfo(output).Length,
                elapsedMs = sw.Elapsed.TotalMilliseconds,
                sourceHeaderCodePage = sourceCodePage,
                rereadHeaderCodePage = reread.Header.CodePage,
                source = before,
                reread = after,
                notifications,
                rereadNotifications,
                editReport,
                storedAcis,
                status = "written_and_reread"
            };
            Console.WriteLine(JsonSerializer.Serialize(report, new JsonSerializerOptions { WriteIndented = true }));
            return 0;
        }
        catch (Exception ex)
        {
            TryDelete(temp);
            Console.Error.WriteLine(JsonSerializer.Serialize(new { input, output, status = "failed", error = ex.ToString(), elapsedMs = sw.Elapsed.TotalMilliseconds }, new JsonSerializerOptions { WriteIndented = true }));
            return 1;
        }
        finally { TryDelete(lockPath); }
    }

    // Read, edit and write the drawing; only plain report data leaves, so the
    // document can be collected before the output is reread.
    private static (SnapshotData before, object? editReport, int storedAcis, string sourceCodePage) WriteEdited(
        string input, string temp, ACadVersion version, string? opsPath, bool edit, List<object> notifications)
    {
        var document = Read(input, notifications);
        var storedAcis = AttachStoredAcis(document);
        document.Header.Version = version;
        object? editReport = opsPath != null
            ? ApplyOperations(document, opsPath)
            : edit ? ApplyEdits(document) : null;
        var before = Snapshot(document);

        using (var writer = new DwgWriter(temp, document))
        {
            writer.Configuration.CloseStream = true;
            writer.OnNotification += (_, e) => notifications.Add(new { phase = "write", type = e.NotificationType.ToString(), e.Message, exception = e.Exception?.ToString() });
            writer.Write();
        }
        return (before, editReport, storedAcis, document.Header.CodePage);
    }

    private static int CreateNew(string outputPath, string versionName, string? opsPath)
    {
        var output = Path.GetFullPath(outputPath);
        if (!TryParseVersion(versionName, out var version)) return Fail($"unsupported target version: {versionName}");
        Directory.CreateDirectory(Path.GetDirectoryName(output)!);
        var temp = output + $".tmp.{Environment.ProcessId}.{Guid.NewGuid():N}";
        try
        {
            var document = new CadDocument(version);
            // AC1018 stores names/text in the file code page; ACadSharp defaults
            // to ANSI_1252, which turns Korean layer names and text into "??".
            // New drawings from ChickenBananaCAD use the Korean code page, as
            // Korean AutoCAD does.  ACadSharp has no "ansi_949" entry; its name
            // for KS C 5601 (DWG code page index 40) is "kcs5601".
            document.Header.CodePage = "kcs5601";
            var editReport = opsPath != null && File.Exists(opsPath) ? ApplyOperations(document, opsPath) : null;
            using (var writer = new DwgWriter(temp, document))
            {
                writer.Configuration.CloseStream = true;
                writer.Write();
            }
            if (!File.Exists(temp) || new FileInfo(temp).Length < 256)
                throw new InvalidDataException("new DWG writer produced an empty file");
            var reread = Read(temp, new List<object>());
            // An empty new drawing is a valid AC1018 document.  Keep the
            // reread guard for operations that actually request entities so
            // a writer regression cannot silently discard user geometry.
            if (reread.ModelSpace.Entities.Count == 0 && HasEntityOperations(opsPath))
                throw new InvalidDataException("new DWG reread has no modelspace entities");
            File.Move(temp, output, true);
            Console.WriteLine(JsonSerializer.Serialize(new {
                output, targetVersion = version.ToString(), outputBytes = new FileInfo(output).Length,
                reread = Snapshot(reread), editReport, status = "created_and_reread"
            }));
            return 0;
        }
        catch (Exception ex)
        {
            TryDelete(temp);
            Console.Error.WriteLine(JsonSerializer.Serialize(new { output, status = "failed", error = ex.ToString() }));
            return 1;
        }
    }

    private static bool HasEntityOperations(string? path)
    {
        if (string.IsNullOrWhiteSpace(path) || !File.Exists(path)) return false;
        using var json = JsonDocument.Parse(File.ReadAllText(path));
        var root = json.RootElement;
        var operations = root.ValueKind == JsonValueKind.Array
            ? root
            : root.TryGetProperty("ops", out var ops) ? ops : default;
        if (operations.ValueKind != JsonValueKind.Array) return false;

        foreach (var operation in operations.EnumerateArray())
        {
            if (!operation.TryGetProperty("type", out var typeProperty)) continue;
            var type = typeProperty.GetString() ?? string.Empty;
            if (type.Equals("add_line", StringComparison.OrdinalIgnoreCase) ||
                type.Equals("add_circle", StringComparison.OrdinalIgnoreCase) ||
                type.Equals("add_arc", StringComparison.OrdinalIgnoreCase) ||
                type.Equals("add_lwpolyline", StringComparison.OrdinalIgnoreCase) ||
                type.Equals("add_text", StringComparison.OrdinalIgnoreCase) ||
                type.Equals("add_mtext", StringComparison.OrdinalIgnoreCase))
                return true;
        }
        return false;
    }

    private static int WriteDxfFromDwg(string inputPath, string outputPath, string? metadataPath = null)
    {
        var input = Path.GetFullPath(inputPath);
        var output = Path.GetFullPath(outputPath);
        if (!File.Exists(input)) return Fail($"input does not exist: {input}");
        if (string.Equals(input, output, StringComparison.OrdinalIgnoreCase)) return Fail("refusing to overwrite input");
        Directory.CreateDirectory(Path.GetDirectoryName(output)!);
        var temp = output + $".tmp.{Environment.ProcessId}.{Guid.NewGuid():N}";
        var lockPath = output + ".lock";
        var notifications = new List<object>();
        var sw = Stopwatch.StartNew();
        try
        {
            using var lockStream = AcquireLock(lockPath, TimeSpan.FromSeconds(DefaultTimeoutSeconds));
            var document = Read(input, notifications);
            // The open API needs the --metadata JSON too; build it from this
            // read (before the DXF writer touches the document) instead of
            // reading the DWG a second time.
            if (metadataPath != null)
                File.WriteAllText(Path.GetFullPath(metadataPath), JsonSerializer.Serialize(BuildMetadata(document, input, notifications), new JsonSerializerOptions { WriteIndented = true }));
            DxfWriter.Write(temp, document, false, notification: (_, e) => notifications.Add(new
            {
                phase = "write-dxf", type = e.NotificationType.ToString(), e.Message,
                exception = e.Exception?.ToString()
            }));
            if (!File.Exists(temp) || new FileInfo(temp).Length < 1024)
                throw new InvalidDataException("DxfWriter produced an empty or implausibly small DXF");
            File.Move(temp, output, true);
            // No DxfReader pass over the output: it only fed this report, which
            // nothing reads, and took most of the open time on large drawings.
            // The open API checks the file and the editor parses it.
            var report = new
            {
                input, output, sourceSha256 = Sha256(input), outputSha256 = Sha256(output),
                sourceBytes = new FileInfo(input).Length, outputBytes = new FileInfo(output).Length,
                elapsedMs = sw.Elapsed.TotalMilliseconds, source = Snapshot(document),
                notifications, status = "dxf_written"
            };
            Console.WriteLine(JsonSerializer.Serialize(report, new JsonSerializerOptions { WriteIndented = true }));
            return 0;
        }
        catch (Exception ex)
        {
            TryDelete(temp);
            Console.Error.WriteLine(JsonSerializer.Serialize(new { input, output, status = "failed", error = ex.ToString(), elapsedMs = sw.Elapsed.TotalMilliseconds }, new JsonSerializerOptions { WriteIndented = true }));
            return 1;
        }
        finally { TryDelete(lockPath); }
    }

    // A DXF opened in the editor becomes an AC1018 DWG first: the editor
    // edits and saves DWGs only, and its own DXF parse keeps neither block
    // definitions nor dimensions.  The report lists, by type, objects the DXF
    // reader skipped and objects missing after the DWG write and reread.
    // A DXF header can name a layer, line type or style its tables do not have
    // (ezdxf writes $DIMSTYLE "ISO-25" without that style); the DWG writer looks
    // the names up and threw, so the whole DXF was refused.  Like AutoCAD on
    // open, those names fall back to the default entries.
    private static void FixMissingHeaderReferences(CadDocument document, List<object> notifications)
    {
        var header = document.Header;
        void Fix(string variable, string name, Func<string, bool> exists, string fallback, IEnumerable<string> names, Action<string> set)
        {
            if (name != null && exists(name)) return;
            var target = exists(fallback) ? fallback : names.FirstOrDefault();
            if (target == null) return;
            set(target);
            notifications.Add(new { phase = "read-dxf", type = "Warning", Message = $"Header {variable} names a missing entry '{name}'; using '{target}'", exception = (string?)null });
        }
        Fix("$CLAYER", header.CurrentLayerName, document.Layers.Contains, "0", document.Layers.Select(x => x.Name), v => header.CurrentLayerName = v);
        Fix("$CELTYPE", header.CurrentLineTypeName, document.LineTypes.Contains, "ByLayer", document.LineTypes.Select(x => x.Name), v => header.CurrentLineTypeName = v);
        Fix("$TEXTSTYLE", header.CurrentTextStyleName, document.TextStyles.Contains, "Standard", document.TextStyles.Select(x => x.Name), v => header.CurrentTextStyleName = v);
        Fix("$DIMSTYLE", header.CurrentDimensionStyleName, document.DimensionStyles.Contains, "Standard", document.DimensionStyles.Select(x => x.Name), v => header.CurrentDimensionStyleName = v);
        Fix("$DIMTXSTY", header.DimensionTextStyleName, document.TextStyles.Contains, "Standard", document.TextStyles.Select(x => x.Name), v => header.DimensionTextStyleName = v);
        Fix("$CMLSTYLE", header.CurrentMLineStyleName, document.MLineStyles.ContainsKey, "Standard", document.MLineStyles.Select(x => x.Name), v => header.CurrentMLineStyleName = v);
    }

    private static int WriteDwgFromDxf(string inputPath, string outputPath)
    {
        var input = Path.GetFullPath(inputPath);
        var output = Path.GetFullPath(outputPath);
        if (!File.Exists(input)) return Fail($"input does not exist: {input}");
        if (string.Equals(input, output, StringComparison.OrdinalIgnoreCase)) return Fail("refusing to overwrite input");
        Directory.CreateDirectory(Path.GetDirectoryName(output)!);
        var temp = output + $".tmp.{Environment.ProcessId}.{Guid.NewGuid():N}";
        var utf8Copy = temp + ".utf8.dxf";
        var lockPath = output + ".lock";
        var notifications = new List<object>();
        var dropped = new Dictionary<string, int>(StringComparer.Ordinal);
        void Drop(string type, int count) => dropped[type] = (dropped.TryGetValue(type, out var n) ? n : 0) + count;
        var sw = Stopwatch.StartNew();
        try
        {
            using var lockStream = AcquireLock(lockPath, TimeSpan.FromSeconds(DefaultTimeoutSeconds));
            var data = File.ReadAllBytes(input);
            var declaresCodePage = data.AsSpan().IndexOf("$DWGCODEPAGE"u8) >= 0;
            var utf8CodePage = declaresCodePage ? DeclaredCodePageOfUtf8Dxf(data) : null;
            var readPath = input;
            if (utf8CodePage != null)
            {
                readPath = utf8Copy;
                File.WriteAllBytes(readPath, WithoutDxfCodePage(data));
            }
            // CreateDefaults adds missing table entries: ChickenBananaCAD's own
            // DXF exports have only a LAYER table, and the DWG writer needs the
            // "Standard" text and dimension styles.  Entries in the file are kept.
            var document = DxfReader.Read(readPath, new DxfReaderConfiguration { CreateDefaults = true }, (_, e) =>
            {
                notifications.Add(new { phase = "read-dxf", type = e.NotificationType.ToString(), e.Message, exception = e.Exception?.ToString() });
                var message = e.Message ?? string.Empty;
                if (message.StartsWith("Entity not supported", StringComparison.Ordinal))
                {
                    var name = message[(message.LastIndexOf(':') + 1)..].Trim();
                    Drop(name.Length > 0 ? name : "ENTITY", 1);
                }
                else if (e.NotificationType == NotificationType.Error && message.StartsWith("Error while reading an entity", StringComparison.Ordinal)) Drop("ENTITY", 1);
                else if (e.NotificationType == NotificationType.Error && message.StartsWith("Error while reading a block", StringComparison.Ordinal)) Drop("BLOCK", 1);
            });
            if (utf8CodePage != null) document.Header.CodePage = utf8CodePage;
            FixMissingHeaderReferences(document, notifications);
            var sourceVersion = document.Header.Version.ToString();
            var sourceCodePage = document.Header.CodePage;
            // A DXF without $DWGCODEPAGE (ChickenBananaCAD's own exports) is read
            // as UTF-8 and keeps ACadSharp's ANSI_1252 default; it gets the
            // Korean code page, as new drawings do.
            if (!declaresCodePage || string.IsNullOrWhiteSpace(document.Header.CodePage)) document.Header.CodePage = "kcs5601";
            document.Header.Version = ACadVersion.AC1018;
            var before = Snapshot(document);
            using (var writer = new DwgWriter(temp, document))
            {
                writer.Configuration.CloseStream = true;
                writer.OnNotification += (_, e) => notifications.Add(new { phase = "write", type = e.NotificationType.ToString(), e.Message, exception = e.Exception?.ToString() });
                writer.Write();
            }
            if (!File.Exists(temp) || new FileInfo(temp).Length < 256)
                throw new InvalidDataException("writer produced an empty or implausibly small DWG");
            var rereadNotifications = new List<object>();
            var after = Snapshot(Read(temp, rereadNotifications));
            foreach (var (type, count) in before.Counts)
            {
                var written = after.Counts.TryGetValue(type, out var n) ? n : 0;
                if (written < count) Drop(type.ToUpperInvariant(), count - written);
            }
            File.Move(temp, output, true);
            Console.WriteLine(JsonSerializer.Serialize(new
            {
                input, output, sourceVersion, sourceCodePage, readAsUtf8 = utf8CodePage != null, codePage = document.Header.CodePage,
                outputBytes = new FileInfo(output).Length, elapsedMs = sw.Elapsed.TotalMilliseconds,
                source = before, reread = after, dropped, notifications, rereadNotifications, status = "dwg_written"
            }, new JsonSerializerOptions { WriteIndented = true }));
            return 0;
        }
        catch (Exception ex)
        {
            TryDelete(temp);
            Console.Error.WriteLine(JsonSerializer.Serialize(new { input, output, status = "failed", error = ex.ToString(), elapsedMs = sw.Elapsed.TotalMilliseconds }, new JsonSerializerOptions { WriteIndented = true }));
            return 1;
        }
        finally { TryDelete(lockPath); TryDelete(utf8Copy); }
    }

    // ChickenBananaCAD's DXF export wrote UTF-8 bytes under $DWGCODEPAGE
    // ANSI_949 until 2026-10-04, so its Korean read as garbage.  A text DXF
    // that declares a code page but is valid UTF-8 with non-ASCII bytes is
    // read as UTF-8 (KS C 5601 text is practically never valid UTF-8).  DXF
    // 2007 and later are UTF-8 anyway.  Returns the declared code page, or
    // null when the file is read as is.
    private static string? DeclaredCodePageOfUtf8Dxf(byte[] data)
    {
        var bytes = data.AsSpan();
        if (bytes.StartsWith("AutoCAD Binary DXF"u8)) return null;
        if (string.CompareOrdinal(DxfHeaderValue(data, "$ACADVER"u8), "AC1021") >= 0) return null;
        if (bytes.IndexOfAnyInRange((byte)0x80, (byte)0xFF) < 0 || !System.Text.Unicode.Utf8.IsValid(bytes)) return null;
        var codePage = DxfHeaderValue(data, "$DWGCODEPAGE"u8);
        return codePage.Length > 0 ? codePage : null;
    }

    // Value of a one-value HEADER variable: name, group code, value lines.
    private static string DxfHeaderValue(byte[] data, ReadOnlySpan<byte> name)
    {
        var at = data.AsSpan().IndexOf(name);
        if (at < 0) return string.Empty;
        var lines = System.Text.Encoding.ASCII.GetString(data, at, Math.Min(200, data.Length - at)).Split('\n');
        return lines.Length >= 3 ? lines[2].Trim() : string.Empty;
    }

    // The copy read hides $DWGCODEPAGE (same length, so nothing else moves),
    // which leaves the DXF reader on UTF-8.
    private static byte[] WithoutDxfCodePage(byte[] data)
    {
        var copy = (byte[])data.Clone();
        var at = copy.AsSpan().IndexOf("$DWGCODEPAGE"u8);
        "$CBLUTF8PAGE"u8.CopyTo(copy.AsSpan(at));
        return copy;
    }

    private static int WriteMetadata(string inputPath)
    {
        var input = Path.GetFullPath(inputPath);
        if (!File.Exists(input)) return Fail($"input does not exist: {input}");
        var notifications = new List<object>();
        try
        {
            var document = Read(input, notifications);
            Console.WriteLine(JsonSerializer.Serialize(BuildMetadata(document, input, notifications), new JsonSerializerOptions { WriteIndented = true }));
            return 0;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine(JsonSerializer.Serialize(new { input, mode = "metadata", status = "failed", error = ex.ToString() }, new JsonSerializerOptions { WriteIndented = true }));
            return 1;
        }
    }

    private static object BuildMetadata(CadDocument document, string input, List<object> notifications)
    {
        var entities = new List<object>();
        AddMetadata(document.ModelSpace.Entities, "modelspace", entities);
        foreach (var block in document.BlockRecords)
        {
            if (block.Name.StartsWith("*Model", StringComparison.OrdinalIgnoreCase) ||
                block.Name.StartsWith("*Paper", StringComparison.OrdinalIgnoreCase)) continue;
            AddMetadata(block.Entities, $"block:{block.Name}", entities);
        }
        var layers = document.Layers
            .Select(layer => new
            {
                name = layer.Name,
                handle = Hex(layer.Handle),
                owner = layer.Owner == null ? null : Hex(layer.Owner.Handle),
                aci = layer.Color.Index,
                trueColor = RgbOf(layer.Color),
                linetype = layer.LineType == null ? null : layer.LineType.Name,
            })
            .OrderBy(layer => layer.handle, StringComparer.Ordinal)
            .ToArray();
        var semanticManifest = BuildSemanticManifest(document, entities);
        var result = new
        {
            mode = "metadata",
            input,
            codePage = document.Header.CodePage,
            layers,
            entities,
            semanticManifest,
            notifications,
            status = "read"
        };
        return result;
    }

    private static object BuildSemanticManifest(CadDocument document, List<object> metadataEntities)
    {
        static string CanonicalType(Entity entity) => entity is Insert insert && insert.IsMultiple
            ? "MINSERT"
            : entity.GetType().Name.ToUpperInvariant();

        static object TypeCounts(IEnumerable<Entity> source) => source
            .GroupBy(CanonicalType, StringComparer.Ordinal)
            .OrderBy(group => group.Key, StringComparer.Ordinal)
            .ToDictionary(group => group.Key, group => group.Count(), StringComparer.Ordinal);

        var blocks = document.BlockRecords
            .Select(block => new
            {
                name = block.Name,
                handle = Hex(block.Handle),
                anonymous = block.IsAnonymous,
                layout = block.Layout?.Name,
                childCount = block.Entities.Count,
                childTypeCounts = TypeCounts(block.Entities),
            })
            .OrderBy(item => item.name, StringComparer.OrdinalIgnoreCase)
            .ToArray();

        var inserts = metadataEntities
            .OfType<Dictionary<string, object?>>()
            .Where(item => string.Equals(item.GetValueOrDefault("type")?.ToString(), "INSERT", StringComparison.OrdinalIgnoreCase)
                        || string.Equals(item.GetValueOrDefault("type")?.ToString(), "MINSERT", StringComparison.OrdinalIgnoreCase))
            .Select(item => new
            {
                handle = item.GetValueOrDefault("handle")?.ToString(),
                space = item.GetValueOrDefault("space")?.ToString(),
                block = item.GetValueOrDefault("block") is Dictionary<string, object?> block
                    ? block.GetValueOrDefault("name")?.ToString()
                    : item.GetValueOrDefault("block")?.GetType().GetProperty("name")?.GetValue(item.GetValueOrDefault("block"))?.ToString()
                        ?? item.GetValueOrDefault("block")?.ToString(),
            })
            .OrderBy(item => item.handle, StringComparer.Ordinal)
            .ToArray();

        var layouts = (document.Layouts ?? Enumerable.Empty<ACadSharp.Objects.Layout>())
            .Select(layout => new
            {
                name = layout.Name,
                paperSpace = layout.IsPaperSpace,
                associatedBlock = layout.AssociatedBlock?.Name,
                entityCount = layout.AssociatedBlock?.Entities.Count ?? 0,
                typeCounts = layout.AssociatedBlock == null
                    ? new Dictionary<string, int>()
                    : (Dictionary<string, int>)TypeCounts(layout.AssociatedBlock.Entities),
            })
            .OrderBy(item => item.name, StringComparer.OrdinalIgnoreCase)
            .ToArray();

        var modelTypeCounts = TypeCounts(document.ModelSpace.Entities);
        var paperTypeCounts = TypeCounts(document.PaperSpace.Entities);
        var insertTargets = new HashSet<string>(
            document.BlockRecords.Select(block => block.Name),
            StringComparer.OrdinalIgnoreCase);
        var unresolvedInsertCount = inserts.Count(item => string.IsNullOrWhiteSpace(item.block) || !insertTargets.Contains(item.block));
        var styleNames = new
        {
            text = document.TextStyles.Select(style => style.Name).OrderBy(name => name, StringComparer.OrdinalIgnoreCase).ToArray(),
            linetype = document.LineTypes.Select(lineType => lineType.Name).OrderBy(name => name, StringComparer.OrdinalIgnoreCase).ToArray(),
            dimension = document.DimensionStyles.Select(style => style.Name).OrderBy(name => name, StringComparer.OrdinalIgnoreCase).ToArray(),
        };
        var unsupported = metadataEntities
            .OfType<Dictionary<string, object?>>()
            .Select(item => item.GetValueOrDefault("type")?.ToString() ?? "")
            .Where(type => type.Length > 0 && type is not ("LINE" or "ARC" or "CIRCLE" or "LWPOLYLINE" or "POLYLINE" or "TEXTENTITY" or "MTEXT" or "DIMENSIONLINEAR" or "DIMENSIONALIGNED" or "DIMENSIONANGULAR" or "DIMENSIONRADIUS" or "DIMENSIONDIAMETER" or "INSERT" or "MINSERT" or "POINT" or "HATCH" or "SOLID" or "3DSOLID" or "REGION"))
            .GroupBy(type => type, StringComparer.OrdinalIgnoreCase)
            .ToDictionary(group => group.Key.ToUpperInvariant(), group => group.Count(), StringComparer.Ordinal);

        return new
        {
            modelspace = new { entityCount = document.ModelSpace.Entities.Count, typeCounts = modelTypeCounts },
            paperspace = new { entityCount = document.PaperSpace.Entities.Count, typeCounts = paperTypeCounts },
            layouts,
            blocks,
            inserts = new { count = inserts.Length, unresolvedCount = unresolvedInsertCount, references = inserts },
            styles = styleNames,
            unsupported,
            // Extents are intentionally reported as unavailable rather than
            // guessed from renderer geometry.  Entity and structure counts
            // remain strict and this field makes that limitation explicit.
            extents = new { available = false },
        };
    }

    private static void AddMetadata(IEnumerable<Entity> source, string space, List<object> result)
    {
        foreach (var entity in source)
        {
            var type = entity is Insert insert && insert.IsMultiple ? "MINSERT" : entity.GetType().Name.ToUpperInvariant();
            var record = new Dictionary<string, object?>
            {
                ["handle"] = Hex(entity.Handle),
                ["type"] = type,
                ["owner"] = entity.Owner == null ? null : Hex(entity.Owner.Handle),
                ["space"] = space,
                ["aci"] = entity.Color.Index,
                ["trueColor"] = RgbOf(entity.Color),
                ["linetype"] = entity.LineType == null ? null : entity.LineType.Name,
                ["lineweight"] = entity.LineWeight.ToString(),
                ["layer"] = entity.Layer == null ? null : new
                {
                    handle = Hex(entity.Layer.Handle),
                    name = entity.Layer.Name,
                    owner = entity.Layer.Owner == null ? null : Hex(entity.Layer.Owner.Handle)
                }
            };
            if (entity is TextEntity text)
            {
                record["text"] = text.Value;
                record["textStyle"] = new { handle = Hex(text.Style.Handle), name = text.Style.Name };
                record["insert"] = new
                {
                    point = new[] { text.InsertPoint.X, text.InsertPoint.Y, text.InsertPoint.Z },
                    height = text.Height,
                    rotation = text.Rotation,
                    widthFactor = text.WidthFactor
                };
                record["alignment"] = new
                {
                    point = new[] { text.AlignmentPoint.X, text.AlignmentPoint.Y, text.AlignmentPoint.Z },
                    horizontal = (int)text.HorizontalAlignment,
                    vertical = (int)text.VerticalAlignment
                };
            }
            else if (entity is MText mtext)
            {
                record["text"] = mtext.Value;
                record["textStyle"] = new { handle = Hex(mtext.Style.Handle), name = mtext.Style.Name };
                record["insert"] = new
                {
                    point = new[] { mtext.InsertPoint.X, mtext.InsertPoint.Y, mtext.InsertPoint.Z },
                    height = mtext.Height,
                    rotation = mtext.Rotation
                };
            }
            if (entity is Insert blockRef)
            {
                record["block"] = blockRef.Block == null ? null : new
                {
                    handle = Hex(blockRef.Block.Handle),
                    name = blockRef.Block.Name
                };
                record["insert"] = new
                {
                    point = new[] { blockRef.InsertPoint.X, blockRef.InsertPoint.Y, blockRef.InsertPoint.Z },
                    scale = new[] { blockRef.XScale, blockRef.YScale, blockRef.ZScale },
                    rotation = blockRef.Rotation,
                    extrusion = new[] { blockRef.Normal.X, blockRef.Normal.Y, blockRef.Normal.Z },
                    rows = blockRef.RowCount,
                    columns = blockRef.ColumnCount,
                    rowSpacing = blockRef.RowSpacing,
                    columnSpacing = blockRef.ColumnSpacing,
                    wasReadAsMInsert = blockRef.WasReadAsMInsert,
                    isMultiple = blockRef.IsMultiple
                };
            }
            result.Add(record);
        }
    }

    private static string Hex(ulong handle) => handle.ToString("X", CultureInfo.InvariantCulture);

    private static object ApplyEdits(CadDocument document)
    {
        var layer = new Layer("CBL_ACADSHARP_POC_EDIT");
        document.Layers.Add(layer);
        var line = new Line(new XYZ(10, 10, 0), new XYZ(20, 10, 0)) { Layer = layer };
        var circle = new Circle(new XYZ(30, 10, 0), 5) { Layer = layer };
        var text = new MText("한글 ACadSharp POC") { InsertPoint = new XYZ(40, 10, 0), Height = 2.5, Layer = layer };
        document.ModelSpace.Entities.Add(line);
        document.ModelSpace.Entities.Add(circle);
        document.ModelSpace.Entities.Add(text);

        var movedLine = document.ModelSpace.Entities.OfType<Line>().FirstOrDefault(x => x != line);
        var changedText = document.ModelSpace.Entities.OfType<TextEntity>().FirstOrDefault();
        var movedInsert = document.ModelSpace.Entities.OfType<Insert>().FirstOrDefault();
        if (movedLine != null)
        {
            movedLine.StartPoint += new XYZ(1, 1, 0);
            movedLine.EndPoint += new XYZ(1, 1, 0);
        }
        if (changedText != null) changedText.Value += " [POC_EDIT]";
        if (movedInsert != null) movedInsert.InsertPoint += new XYZ(1, 1, 0);

        var plainBlock = document.BlockRecords.FirstOrDefault(x =>
            !x.Name.StartsWith("*", StringComparison.OrdinalIgnoreCase));
        Insert? addedPlainInsert = null;
        if (plainBlock != null)
        {
            addedPlainInsert = new Insert(plainBlock)
            {
                InsertPoint = new XYZ(60, 10, 0),
                Layer = layer
            };
            document.ModelSpace.Entities.Add(addedPlainInsert);
        }

        return new
        {
            addedLine = true,
            addedCircle = true,
            addedKoreanMText = true,
            addedLayer = layer.Name,
            addedPlainInsert = addedPlainInsert?.Handle.ToString("X"),
            addedPlainInsertIsMultiple = addedPlainInsert?.IsMultiple,
            movedLine = movedLine?.Handle.ToString("X"),
            changedText = changedText?.Handle.ToString("X"),
            movedInsert = movedInsert?.Handle.ToString("X")
        };
    }

    private static object ApplyOperations(CadDocument document, string path)
    {
        using var json = JsonDocument.Parse(File.ReadAllText(path));
        var root = json.RootElement;
        var operations = root.ValueKind == JsonValueKind.Array
            ? root
            : root.TryGetProperty("ops", out var ops) ? ops : throw new InvalidDataException("ops array is required");
        var applied = new List<object>();

        // The ACadSharp reader can decode legacy Korean STYLE names using a
        // different code page than the browser's source DXF.  Restore the
        // canonical STYLE table metadata before writing, without counting
        // style synchronization as an entity edit operation.
        if (root.ValueKind == JsonValueKind.Object && root.TryGetProperty("textStyles", out var textStyles) && textStyles.ValueKind == JsonValueKind.Array)
        {
            foreach (var styleOp in textStyles.EnumerateArray())
                SyncTextStyle(document, styleOp);
        }

        foreach (var op in operations.EnumerateArray())
        {
            var type = RequiredString(op, "type").ToLowerInvariant();
            switch (type)
            {
                case "create_layer":
                {
                    var name = SafeName(RequiredString(op, "name"));
                    var layer = document.Layers.FirstOrDefault(x => x.Name == name);
                    if (layer == null)
                    {
                        var aci = ReadInt(op, "color", 7);
                        if (aci <= 0 || aci >= 256) aci = 7;
                        layer = new Layer(name) { Color = new Color((short)aci) };
                        document.Layers.Add(layer);
                    }
                    applied.Add(new { type, name, created = true });
                    break;
                }
                case "update_layer":
                {
                    // Layer colour, linetype, lineweight, on/off and lock edited in the layer panel.
                    var name = SafeName(RequiredString(op, "name"));
                    var layer = document.Layers.FirstOrDefault(x => string.Equals(x.Name, name, StringComparison.OrdinalIgnoreCase))
                        ?? throw new InvalidDataException($"Layer not found: {name}");
                    if (op.TryGetProperty("trueColor", out var trueColor) && trueColor.ValueKind == JsonValueKind.Number &&
                        trueColor.TryGetUInt32(out var rgb))
                    {
                        layer.Color = ColorFromRgb(rgb);
                    }
                    else if (op.TryGetProperty("aci", out var aciValue) && aciValue.ValueKind == JsonValueKind.Number)
                    {
                        var aci = aciValue.GetInt32();
                        if (aci < 1 || aci > 255) throw new InvalidDataException($"Layer colour must be ACI 1-255: {aci}");
                        layer.Color = new Color((short)aci);
                    }
                    if (op.TryGetProperty("linetype", out var lineTypeValue) && lineTypeValue.ValueKind == JsonValueKind.String)
                    {
                        var lineTypeName = CanonicalLineTypeName(lineTypeValue.GetString());
                        var lineType = document.LineTypes.FirstOrDefault(x => string.Equals(x.Name, lineTypeName, StringComparison.OrdinalIgnoreCase))
                            ?? throw new InvalidDataException($"Linetype not found: {lineTypeName}");
                        layer.LineType = lineType;
                    }
                    if (op.TryGetProperty("lineweight", out var lineWeightValue) && lineWeightValue.ValueKind == JsonValueKind.Number)
                    {
                        var lineWeight = (LineWeightType)lineWeightValue.GetInt32();
                        if (!Enum.IsDefined(lineWeight) || lineWeight == LineWeightType.ByLayer || lineWeight == LineWeightType.ByBlock)
                            throw new InvalidDataException($"Invalid layer lineweight: {lineWeightValue.GetInt32()}");
                        layer.LineWeight = lineWeight;
                    }
                    if (op.TryGetProperty("on", out var onValue) && (onValue.ValueKind == JsonValueKind.True || onValue.ValueKind == JsonValueKind.False))
                        layer.IsOn = onValue.GetBoolean();
                    if (op.TryGetProperty("locked", out var lockedValue) && (lockedValue.ValueKind == JsonValueKind.True || lockedValue.ValueKind == JsonValueKind.False))
                        layer.Flags = lockedValue.GetBoolean() ? layer.Flags | LayerFlags.Locked : layer.Flags & ~LayerFlags.Locked;
                    applied.Add(new { type, name });
                    break;
                }
                case "add_line":
                {
                    var layer = ResolveLayer(document, op);
                    var line = new Line(ReadPoint(op, "start"), ReadPoint(op, "end")) { Layer = layer };
                    ApplyEntityDisplayProperties(document, line, op);
                    document.ModelSpace.Entities.Add(line);
                    applied.Add(new { type, handle = line.Handle.ToString("X") });
                    break;
                }
                case "add_circle":
                {
                    var layer = ResolveLayer(document, op);
                    var circle = new Circle(ReadPoint(op, "center"), ReadDouble(op, "radius", 1)) { Layer = layer };
                    ApplyEntityDisplayProperties(document, circle, op);
                    document.ModelSpace.Entities.Add(circle);
                    applied.Add(new { type, handle = circle.Handle.ToString("X") });
                    break;
                }
                case "add_arc":
                {
                    var layer = ResolveLayer(document, op);
                    // Angles are radians, counter-clockwise from start to end (DXF convention).
                    var arc = new Arc(ReadPoint(op, "center"), ReadDouble(op, "radius", 1),
                        ReadDouble(op, "startAngle", 0), ReadDouble(op, "endAngle", Math.PI)) { Layer = layer };
                    ApplyEntityDisplayProperties(document, arc, op);
                    document.ModelSpace.Entities.Add(arc);
                    applied.Add(new { type, handle = arc.Handle.ToString("X") });
                    break;
                }
                case "add_insert":
                {
                    var insert = CreateInsert(document, op);
                    insert.Layer = ResolveLayer(document, op);
                    ApplyEntityDisplayProperties(document, insert, op);
                    document.ModelSpace.Entities.Add(insert);
                    applied.Add(new { type, handle = insert.Handle.ToString("X") });
                    break;
                }
                case "add_lwpolyline":
                {
                    var points = op.GetProperty("points").EnumerateArray()
                        .Select(x => new LwPolyline.Vertex(ReadDouble(x, 0), ReadDouble(x, 1))).ToArray();
                    if (points.Length < 2) throw new InvalidDataException("add_lwpolyline requires two points");
                    ApplyBulges(points, op, null);
                    var poly = new LwPolyline(points) { IsClosed = ReadBool(op, "closed") };
                    poly.Layer = ResolveLayer(document, op);
                    ApplyEntityDisplayProperties(document, poly, op);
                    document.ModelSpace.Entities.Add(poly);
                    applied.Add(new { type, handle = poly.Handle.ToString("X"), points = points.Length });
                    break;
                }
                case "add_text":
                case "add_mtext":
                {
                    var layer = ResolveLayer(document, op);
                    var value = op.TryGetProperty("text", out var text) ? text.GetString() : op.GetProperty("value").GetString();
                    if (type == "add_mtext")
                    {
                        var entity = new MText(value ?? string.Empty) { InsertPoint = ReadPoint(op, "insert"), Layer = layer };
                        entity.Height = ReadDouble(op, "height", 250);
                        ApplyEntityDisplayProperties(document, entity, op);
                        ApplyTextStyle(document, entity, op);
                        document.ModelSpace.Entities.Add(entity);
                        applied.Add(new { type, handle = entity.Handle.ToString("X") });
                    }
                    else
                    {
                        var entity = new TextEntity { Value = value ?? string.Empty, InsertPoint = ReadPoint(op, "insert"), Layer = layer };
                        entity.Height = ReadDouble(op, "height", 250);
                        entity.Rotation = ReadDouble(op, "rotation", 0);
                        ApplyEntityDisplayProperties(document, entity, op);
                        ApplyTextStyle(document, entity, op);
                        document.ModelSpace.Entities.Add(entity);
                        applied.Add(new { type, handle = entity.Handle.ToString("X") });
                    }
                    break;
                }
                case "add_dimension":
                {
                    var dimension = CreateDimension(document, op);
                    document.ModelSpace.Entities.Add(dimension);
                    // ACadSharp stores the visible dimension geometry in the
                    // anonymous dimension block.  Build it after the entity
                    // is attached to the document so the written DIMENSION
                    // has its block reference and can be rendered on reload.
                    dimension.UpdateBlock();
                    applied.Add(new { type, kind = dimension is DimensionLinear ? "linear" : "aligned", handle = dimension.Handle.ToString("X") });
                    break;
                }
                case "transform":
                {
                    // Rotate / scale / mirror from the editor, as one plan similarity.
                    var entity = FindModelEntity(document, RequiredString(op, "handle"), op);
                    TransformEntity(entity, ReadSimilarity(op));
                    applied.Add(new { type, handle = entity.Handle.ToString("X") });
                    break;
                }
                case "add_copy":
                {
                    // A copy keeps the source's kind (hatch, spline, dimension ...):
                    // the source is cloned, added and placed by the copy's transform.
                    var source = FromOpened(op) ? FindRestoredEntity(op) : FindModelEntity(document, RequiredString(op, "copyOf"), op);
                    var copy = CopyEntity(document, source);
                    TransformEntity(copy, ReadSimilarity(op));
                    applied.Add(new { type, handle = copy.Handle.ToString("X"), copyOf = source.Handle.ToString("X"), sourceBlock = (source as Dimension)?.Block?.Name });
                    break;
                }
                case "move":
                case "update":
                case "delete":
                {
                    var entity = FindModelEntity(document, RequiredString(op, "handle"), op);
                    if (type == "delete")
                    {
                        document.ModelSpace.Entities.Remove(entity);
                        applied.Add(new { type, handle = entity.Handle.ToString("X") });
                        break;
                    }
                    if (type == "move")
                    {
                        var delta = ReadPoint(op, "delta");
                        MoveEntity(entity, delta);
                    }
                    else
                    {
                        UpdateEntity(document, entity, op);
                        if (entity is Dimension dimension) dimension.UpdateBlock();
                    }
                    applied.Add(new { type, handle = entity.Handle.ToString("X") });
                    break;
                }
                default:
                    throw new NotSupportedException($"Unsupported edit operation: {type}");
            }
        }

        return new { operationCount = applied.Count, applied };
    }

    private static Entity FindModelEntity(CadDocument document, string rawHandle, JsonElement? operation = null)
    {
        var canonical = NormalizeHandle(rawHandle);
        if (string.IsNullOrEmpty(canonical) || !ulong.TryParse(canonical, NumberStyles.HexNumber, CultureInfo.InvariantCulture, out var handle))
            throw new InvalidDataException($"Invalid entity handle: {rawHandle}");
        var entity = document.ModelSpace.Entities.FirstOrDefault(x => x.Handle == handle);
        // ACadSharp and LibreDWG can assign different handles while reading
        // the same source INSERT.  The browser operation carries the stable
        // INSERT identity; resolve only an unambiguous block/name+point match.
        if (entity == null && operation is { } op &&
            op.TryGetProperty("entity", out var entityType))
        {
            if (string.Equals(entityType.GetString(), "INSERT", StringComparison.OrdinalIgnoreCase) &&
                op.TryGetProperty("blockName", out var blockName) && op.TryGetProperty("insert", out _))
            {
                var wantedName = blockName.GetString() ?? string.Empty;
                var wantedPoint = ReadPoint(op, "insert");
                var matches = document.ModelSpace.Entities.OfType<Insert>()
                    .Where(x => x.Block != null && string.Equals(x.Block.Name, wantedName, StringComparison.OrdinalIgnoreCase))
                    .Where(x => Math.Abs(x.InsertPoint.X - wantedPoint.X) <= 1e-5 && Math.Abs(x.InsertPoint.Y - wantedPoint.Y) <= 1e-5)
                    .ToList();
                if (matches.Count == 1) entity = matches[0];
            }
            else if (string.Equals(entityType.GetString(), "LINE", StringComparison.OrdinalIgnoreCase) &&
                     op.TryGetProperty("start", out _) && op.TryGetProperty("end", out _))
            {
                var wantedStart = ReadPoint(op, "start");
                var wantedEnd = ReadPoint(op, "end");
                var matches = document.ModelSpace.Entities.OfType<Line>().Where(x =>
                    (Math.Abs(x.StartPoint.X - wantedStart.X) <= 1e-5 && Math.Abs(x.StartPoint.Y - wantedStart.Y) <= 1e-5 &&
                     Math.Abs(x.EndPoint.X - wantedEnd.X) <= 1e-5 && Math.Abs(x.EndPoint.Y - wantedEnd.Y) <= 1e-5) ||
                    (Math.Abs(x.StartPoint.X - wantedEnd.X) <= 1e-5 && Math.Abs(x.StartPoint.Y - wantedEnd.Y) <= 1e-5 &&
                     Math.Abs(x.EndPoint.X - wantedStart.X) <= 1e-5 && Math.Abs(x.EndPoint.Y - wantedStart.Y) <= 1e-5))
                    .ToList();
                if (matches.Count == 1) entity = matches[0];
            }
        }
        if (entity == null) throw new InvalidDataException($"Modelspace entity not found: {rawHandle}");
        if (entity is Region) throw new NotSupportedException("REGION editing is not supported");
        return entity;
    }

    private static string NormalizeHandle(string? value)
    {
        if (string.IsNullOrWhiteSpace(value)) return string.Empty;
        var text = value.Trim();
        if (text.StartsWith("0x", StringComparison.OrdinalIgnoreCase)) text = text[2..];
        text = text.ToUpperInvariant();
        if (text.Length == 0 || text.Any(c => !Uri.IsHexDigit(c))) return string.Empty;
        text = text.TrimStart('0');
        return text.Length == 0 ? "0" : text;
    }

    private static void MoveEntity(Entity entity, XYZ delta)
    {
        switch (entity)
        {
            case Line line: line.StartPoint += delta; line.EndPoint += delta; break;
            case Circle circle: circle.Center += delta; break;
            case LwPolyline poly:
                foreach (var vertex in poly.Vertices) vertex.Location += new XY(delta.X, delta.Y);
                break;
            case TextEntity text: text.InsertPoint += delta; break;
            case MText mtext: mtext.InsertPoint += delta; break;
            case Insert insert:
                insert.InsertPoint += delta;
                MoveAttributes(insert, delta);
                break;
            // The kinds below are drawn by the editor but not rebuilt by it, so a
            // move is all it can ask for.  Only points move; ACadSharp's
            // ApplyTransform is not used because it also translates direction
            // vectors (spline tangents, the ellipse minor axis, the hatch
            // pattern axis) and does nothing for a MULTILEADER.
            case Spline spline:
                for (int i = 0; i < spline.ControlPoints.Count; i++) spline.ControlPoints[i] += delta;
                for (int i = 0; i < spline.FitPoints.Count; i++) spline.FitPoints[i] += delta;
                break;
            case Ellipse ellipse: ellipse.Center += delta; break;
            case Solid solid:
                RequirePlanNormal(solid.Normal, solid);
                solid.FirstCorner += delta; solid.SecondCorner += delta; solid.ThirdCorner += delta; solid.FourthCorner += delta;
                break;
            case Face3D face:
                face.FirstCorner += delta; face.SecondCorner += delta; face.ThirdCorner += delta; face.FourthCorner += delta;
                break;
            case Point point: point.Location += delta; break;
            case Leader leader:
                for (int i = 0; i < leader.Vertices.Count; i++) leader.Vertices[i] += delta;
                break;
            case Hatch hatch: MoveHatch(hatch, delta); break;
            case Dimension dimension: MoveDimension(dimension, delta); break;
            case MultiLeader multiLeader: MoveMultiLeader(multiLeader, delta); break;
            default: throw new NotSupportedException($"Move is not supported for {entity.GetType().Name}");
        }
    }

    // A plan similarity x' = a·x + c·y + e, y' = b·x + d·y + f: the editor's
    // rotate, uniform scale and mirror (with any move), never a shear.
    private readonly struct Similarity
    {
        public readonly double A, B, C, D, E, F;
        public Similarity(double a, double b, double c, double d, double e, double f) { A = a; B = b; C = c; D = d; E = e; F = f; }
        public double Determinant => A * D - B * C;
        public double Scale => Math.Sqrt(Math.Abs(Determinant));
        public bool Mirror => Determinant < 0;
        public double Rotation => Math.Atan2(B, A);
        public bool Linear => Math.Abs(A - 1) < 1e-12 && Math.Abs(B) < 1e-12 && Math.Abs(C) < 1e-12 && Math.Abs(D - 1) < 1e-12;
        public XYZ Point(XYZ p) => new XYZ(A * p.X + C * p.Y + E, B * p.X + D * p.Y + F, p.Z);
        public XY Point(XY p) => new XY(A * p.X + C * p.Y + E, B * p.X + D * p.Y + F);
        public XYZ Vector(XYZ v) => new XYZ(A * v.X + C * v.Y, B * v.X + D * v.Y, v.Z);
        public XY Vector(XY v) => new XY(A * v.X + C * v.Y, B * v.X + D * v.Y);
        // The image of an angle measured from the X axis (radians).
        public double Angle(double angle) => Rotation + (Mirror ? -angle : angle);
    }

    private static Similarity ReadSimilarity(JsonElement op)
    {
        var m = op.GetProperty("matrix");
        if (m.GetArrayLength() != 6) throw new InvalidDataException("matrix needs [a, b, c, d, e, f]");
        var t = new Similarity(ReadDouble(m, 0), ReadDouble(m, 1), ReadDouble(m, 2), ReadDouble(m, 3), ReadDouble(m, 4), ReadDouble(m, 5));
        double u = t.A * t.A + t.B * t.B, v = t.C * t.C + t.D * t.D;
        if (u < 1e-24 || Math.Abs(u - v) > 1e-9 * Math.Max(u, v) || Math.Abs(t.A * t.C + t.B * t.D) > 1e-9 * Math.Max(u, v))
            throw new NotSupportedException("Transform is not supported for a non-uniform scale or shear");
        return t;
    }

    private static void TransformEntity(Entity entity, Similarity m)
    {
        if (m.Linear)
        {
            if (Math.Abs(m.E) > 0 || Math.Abs(m.F) > 0) MoveEntity(entity, new XYZ(m.E, m.F, 0));
            return;
        }
        double s = m.Scale;
        switch (entity)
        {
            case Line line: line.StartPoint = m.Point(line.StartPoint); line.EndPoint = m.Point(line.EndPoint); break;
            case Arc arc:
            {
                RequirePlanNormal(arc.Normal, arc);
                double start = arc.StartAngle, end = arc.EndAngle;
                arc.Center = m.Point(arc.Center);
                arc.Radius *= s;
                // A mirrored counter-clockwise arc runs the other way: swap its ends.
                arc.StartAngle = m.Mirror ? m.Angle(end) : m.Angle(start);
                arc.EndAngle = m.Mirror ? m.Angle(start) : m.Angle(end);
                break;
            }
            case Circle circle: RequirePlanNormal(circle.Normal, circle); circle.Center = m.Point(circle.Center); circle.Radius *= s; break;
            case LwPolyline poly:
                RequirePlanNormal(poly.Normal, poly);
                foreach (var vertex in poly.Vertices)
                {
                    vertex.Location = m.Point(vertex.Location);
                    if (m.Mirror) vertex.Bulge = -vertex.Bulge;
                }
                break;
            case Spline spline:
                for (int i = 0; i < spline.ControlPoints.Count; i++) spline.ControlPoints[i] = m.Point(spline.ControlPoints[i]);
                for (int i = 0; i < spline.FitPoints.Count; i++) spline.FitPoints[i] = m.Point(spline.FitPoints[i]);
                spline.StartTangent = m.Vector(spline.StartTangent);
                spline.EndTangent = m.Vector(spline.EndTangent);
                break;
            case Ellipse ellipse:
            {
                RequirePlanNormal(ellipse.Normal, ellipse);
                double start = ellipse.StartParameter, end = ellipse.EndParameter;
                ellipse.Center = m.Point(ellipse.Center);
                ellipse.MajorAxisEndPoint = m.Vector(ellipse.MajorAxisEndPoint);
                // Mirrored, the point at parameter t is at -t of the new axes.
                if (m.Mirror)
                {
                    // Keep the span: a full ellipse stays 0..2π.
                    ellipse.StartParameter = NormalizeAngle(-end);
                    ellipse.EndParameter = ellipse.StartParameter + (end - start);
                }
                break;
            }
            case Solid solid:
                RequirePlanNormal(solid.Normal, solid);
                solid.FirstCorner = m.Point(solid.FirstCorner); solid.SecondCorner = m.Point(solid.SecondCorner);
                solid.ThirdCorner = m.Point(solid.ThirdCorner); solid.FourthCorner = m.Point(solid.FourthCorner);
                break;
            case Face3D face:
                face.FirstCorner = m.Point(face.FirstCorner); face.SecondCorner = m.Point(face.SecondCorner);
                face.ThirdCorner = m.Point(face.ThirdCorner); face.FourthCorner = m.Point(face.FourthCorner);
                break;
            case Point point: point.Location = m.Point(point.Location); break;
            case Leader leader:
                for (int i = 0; i < leader.Vertices.Count; i++) leader.Vertices[i] = m.Point(leader.Vertices[i]);
                leader.HorizontalDirection = m.Vector(leader.HorizontalDirection).Normalize();
                break;
            case TextEntity text:
                if (m.Mirror) throw new NotSupportedException("Transform is not supported for TextEntity (mirrored)");
                text.InsertPoint = m.Point(text.InsertPoint);
                text.AlignmentPoint = m.Point(text.AlignmentPoint);
                text.Rotation += m.Rotation;
                text.Height *= s;
                break;
            case MText mtext:
                if (m.Mirror) throw new NotSupportedException("Transform is not supported for MText (mirrored)");
                mtext.InsertPoint = m.Point(mtext.InsertPoint);
                mtext.AlignmentPoint = m.Vector(mtext.AlignmentPoint).Normalize();
                mtext.Height *= s;
                mtext.RectangleWidth *= s;
                break;
            case Insert insert:
                if (m.Mirror || insert.Attributes.Any()) throw new NotSupportedException("Transform is not supported for Insert (mirrored or with attributes)");
                insert.InsertPoint = m.Point(insert.InsertPoint);
                insert.Rotation += m.Rotation;
                insert.XScale *= s; insert.YScale *= s; insert.ZScale *= s;
                break;
            case Hatch hatch: TransformHatch(hatch, m); break;
            case Dimension dimension: TransformDimension(dimension, m); break;
            case MultiLeader multiLeader: TransformMultiLeader(multiLeader, m); break;
            default: throw new NotSupportedException($"Transform is not supported for {entity.GetType().Name}");
        }
    }

    private static double NormalizeAngle(double angle)
    {
        angle %= 2 * Math.PI;
        return angle < 0 ? angle + 2 * Math.PI : angle;
    }

    private static void TransformHatch(Hatch hatch, Similarity m)
    {
        RequirePlanNormal(hatch.Normal, hatch);
        bool patterned = !hatch.IsSolid && hatch.Pattern.Lines.Count > 0;
        double s = m.Scale;
        foreach (var path in hatch.Paths)
        {
            foreach (var edge in path.Edges)
            {
                switch (edge)
                {
                    case Hatch.BoundaryPath.Line line: line.Start = m.Point(line.Start); line.End = m.Point(line.End); break;
                    case Hatch.BoundaryPath.Arc arc:
                    {
                        // A clockwise edge stores its angles negated (AutoCAD; ACadSharp's
                        // own transform reads them so): turn the real angles, and a
                        // mirror makes the edge run the other way.
                        arc.Center = m.Point(arc.Center);
                        arc.Radius *= s;
                        double start = m.Angle(arc.CounterClockWise ? arc.StartAngle : -arc.StartAngle);
                        double end = m.Angle(arc.CounterClockWise ? arc.EndAngle : -arc.EndAngle);
                        arc.CounterClockWise ^= m.Mirror;
                        arc.StartAngle = arc.CounterClockWise ? start : -start;
                        arc.EndAngle = arc.CounterClockWise ? end : -end;
                        break;
                    }
                    case Hatch.BoundaryPath.Ellipse ellipseEdge:
                        // Parameters are measured from the (moved) major axis.  A
                        // mirror reverses them and the direction; with the negated
                        // storage of clockwise edges the stored values stay.
                        ellipseEdge.Center = m.Point(ellipseEdge.Center);
                        ellipseEdge.MajorAxisEndPoint = m.Vector(ellipseEdge.MajorAxisEndPoint);
                        ellipseEdge.CounterClockWise ^= m.Mirror;
                        break;
                    case Hatch.BoundaryPath.Spline splineEdge:
                        // Z of a spline edge control point is its weight.
                        for (int i = 0; i < splineEdge.ControlPoints.Count; i++)
                        {
                            var p = splineEdge.ControlPoints[i];
                            var q = m.Point(new XY(p.X, p.Y));
                            splineEdge.ControlPoints[i] = new XYZ(q.X, q.Y, p.Z);
                        }
                        for (int i = 0; i < splineEdge.FitPoints.Count; i++) splineEdge.FitPoints[i] = m.Point(splineEdge.FitPoints[i]);
                        splineEdge.StartTangent = m.Vector(splineEdge.StartTangent);
                        splineEdge.EndTangent = m.Vector(splineEdge.EndTangent);
                        break;
                    case Hatch.BoundaryPath.Polyline polyline:
                        // Z of a polyline path vertex is its bulge.
                        for (int i = 0; i < polyline.Vertices.Count; i++)
                        {
                            var v = polyline.Vertices[i];
                            var q = m.Point(new XY(v.X, v.Y));
                            polyline.Vertices[i] = new XYZ(q.X, q.Y, m.Mirror ? -v.Z : v.Z);
                        }
                        break;
                    default: throw new NotSupportedException($"Transform is not supported for hatch edge {edge.GetType().Name}");
                }
            }
        }
        for (int i = 0; i < hatch.SeedPoints.Count; i++) hatch.SeedPoints[i] = m.Point(hatch.SeedPoints[i]);
        if (patterned)
        {
            // Each pattern line family turns (a mirror reverses its angle): the
            // lines go through the moved base point, spaced by the moved offset;
            // the dashes run along the line from the base point as before.
            foreach (var line in hatch.Pattern.Lines)
            {
                line.Angle = m.Angle(line.Angle);
                line.BasePoint = m.Point(line.BasePoint);
                line.Offset = m.Vector(line.Offset);
                for (int i = 0; i < line.DashLengths.Count; i++) line.DashLengths[i] *= s;
            }
            // The PatternAngle/PatternScale setters rebuild the lines; set the values only.
            SetField(hatch, "_patternAngle", NormalizeAngle(m.Angle(hatch.PatternAngle)));
            SetField(hatch, "_patternScale", hatch.PatternScale * s);
        }
    }

    private static void SetField(object target, string name, object value)
    {
        var field = target.GetType().GetField(name, System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic)
            ?? throw new MissingFieldException(target.GetType().Name, name);
        field.SetValue(target, value);
    }

    // A dimension shows its stored measurement, so only its position and
    // direction may change; scaled or mirrored it would show a wrong value.
    private static void TransformDimension(Dimension dimension, Similarity m)
    {
        if (m.Mirror) throw new NotSupportedException("Transform is not supported for Dimension (mirrored)");
        // An ordinate dimension measures along the drawing's axes from its
        // origin: turned, its number would be wrong.
        var turn = NormalizeAngle(m.Rotation);
        if (dimension is DimensionOrdinate && Math.Min(turn, 2 * Math.PI - turn) > 1e-9)
            throw new NotSupportedException("Transform is not supported for Dimension (ordinate rotated)");
        var scaled = Math.Abs(m.Scale - 1) > 1e-9;
        var style = dimension.GetActiveDimensionStyle();
        // ACadSharp's IsAngular reads the type as flags (an ordinate or diameter
        // dimension counts as angular), so the angle kinds are named here; its
        // own measurement text is used only where that flag is right.
        var angular = dimension is DimensionAngular2Line || dimension is DimensionAngular3Pt;
        var oldText = scaled && !angular ? (dimension.IsAngular ? "" : dimension.GetMeasurementText(style)) : null;
        var oldValue = dimension.Measurement;
        foreach (var property in dimension.GetType().GetProperties())
        {
            if (property.PropertyType != typeof(XYZ) || !property.CanRead || !property.CanWrite || property.Name == nameof(Dimension.Normal)) continue;
            property.SetValue(dimension, m.Point((XYZ)property.GetValue(dimension)!));
        }
        if (dimension is DimensionLinear linear) linear.Rotation += m.Rotation;
        if (Math.Abs(dimension.TextRotation) > 1e-12) dimension.TextRotation += m.Rotation;
        var block = dimension.Block;
        if (block == null) return;
        var document = dimension.Document;
        if (document != null && document.Entities.OfType<Dimension>().Count(d => ReferenceEquals(d.Block, block)) > 1)
            throw new NotSupportedException("Transform is not supported for Dimension (shared block)");
        foreach (var entity in block.Entities.ToList()) TransformEntity(entity, m);
        if (oldText != null) UpdateMeasurementText(dimension, style, oldText, oldValue);
    }

    // A scaled dimension shows its new length, as when AutoCAD regenerates it:
    // the number in its block text that is the old measurement becomes the new
    // one, in the same format (the style's own text, or the drawing's digits,
    // decimals and thousands commas).  A text override without "<>" stays, as
    // in AutoCAD; a number that cannot be found is refused, not left wrong.
    private static void UpdateMeasurementText(Dimension dimension, DimensionStyle style, string oldText, double oldValue)
    {
        if (!string.IsNullOrEmpty(dimension.Text) && !dimension.Text.Contains("<>")) return;
        if (style.AlternateUnitDimensioning)
            throw new NotSupportedException("Transform is not supported for Dimension (scaled with alternate units)");
        var texts = dimension.Block!.Entities.Where(e => e is MText || e is TextEntity).ToList();
        string Read(Entity e) => e is MText mt ? mt.Value : ((TextEntity)e).Value;
        void Write(Entity e, string value) { if (e is MText mt) mt.Value = value; else ((TextEntity)e).Value = value; }
        Regex Standalone(string token) => new Regex(@"(?<![\d.,])" + Regex.Escape(token) + @"(?![\d,]|\.\d)");
        var newText = oldText.Length > 0 ? dimension.GetMeasurementText(style) : "";
        var found = oldText.Length > 0 ? texts.SelectMany(e => Standalone(oldText).Matches(Read(e)).Select(x => (e, x))).ToList() : new();
        if (oldText.Length > 0 && found.Count == 1)
        {
            var (entity, match) = found[0];
            var value = Read(entity);
            Write(entity, value[..match.Index] + newText + value[(match.Index + match.Length)..]);
            return;
        }
        // The drawing's own format (e.g. "4,780"): the one number equal to the
        // old measurement at its decimals, rewritten at the same decimals.
        double before = style.ApplyRounding(oldValue) * style.LinearScaleFactor;
        double after = style.ApplyRounding(dimension.Measurement) * style.LinearScaleFactor;
        var number = new Regex(@"(?<![\d.,])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(?![\d,]|\.\d)");
        var candidates = texts.SelectMany(e => number.Matches(Read(e)).Where(x =>
        {
            var decimals = x.Groups[2].Success ? x.Groups[2].Length : 0;
            var parsed = double.Parse(x.Value.Replace(",", ""), CultureInfo.InvariantCulture);
            return Math.Abs(parsed - Math.Round(before, decimals)) <= 0.5 * Math.Pow(10, -decimals) + 1e-9;
        }).Select(x => (e, x))).ToList();
        if (candidates.Count != 1)
            throw new NotSupportedException("Transform is not supported for Dimension (measurement text not found)");
        var (target, token) = candidates[0];
        var places = token.Groups[2].Success ? token.Groups[2].Length : 0;
        var formatted = Math.Round(after, places).ToString((token.Groups[1].Value.Contains(',') ? "N" : "F") + places, CultureInfo.InvariantCulture);
        var text = Read(target);
        Write(target, text[..token.Index] + formatted + text[(token.Index + token.Length)..]);
    }

    private static void TransformMultiLeader(MultiLeader leader, Similarity m)
    {
        var context = leader.ContextData;
        if (context.HasContentsBlock)
            throw new NotSupportedException("Transform is not supported for MultiLeader (mirrored or with block content)");
        // Mirrored, the text stays readable (AutoCAD's MIRRTEXT 0): it keeps its
        // direction and goes to the other side of the mirrored landing, its
        // left and right attachment swapped -- the mirror of the text box when
        // the mirror turns the text direction around.  Upside down or at a
        // slant AutoCAD rebuilds the text from the landing; that is refused.
        if (m.Mirror && (m.Vector(context.Direction).Normalize() + context.Direction.Normalize()).GetLength() > 1e-6)
            throw new NotSupportedException("Transform is not supported for MultiLeader (mirrored upside down or at a slant)");
        double s = m.Scale;
        context.ContentBasePoint = m.Point(context.ContentBasePoint);
        context.TextLocation = m.Point(context.TextLocation);
        context.BasePoint = m.Point(context.BasePoint);
        if (m.Mirror)
        {
            static TextAttachmentPointType Side(TextAttachmentPointType t) => t == TextAttachmentPointType.Left ? TextAttachmentPointType.Right : t == TextAttachmentPointType.Right ? TextAttachmentPointType.Left : t;
            static TextAlignmentType Align(TextAlignmentType t) => t == TextAlignmentType.Left ? TextAlignmentType.Right : t == TextAlignmentType.Right ? TextAlignmentType.Left : t;
            context.TextAttachmentPoint = Side(context.TextAttachmentPoint);
            leader.TextAttachmentPoint = Side(leader.TextAttachmentPoint);
            context.TextAlignment = Align(context.TextAlignment);
            leader.TextAlignment = Align(leader.TextAlignment);
        }
        else
        {
            context.Direction = m.Vector(context.Direction).Normalize();
            context.BaseDirection = m.Vector(context.BaseDirection).Normalize();
            context.BaseVertical = m.Vector(context.BaseVertical).Normalize();
            context.TextRotation += m.Rotation;
        }
        context.TextHeight *= s;
        context.ArrowheadSize *= s;
        context.LandingGap *= s;
        context.BoundaryWidth *= s;
        foreach (var root in context.LeaderRoots)
        {
            root.ConnectionPoint = m.Point(root.ConnectionPoint);
            root.Direction = m.Vector(root.Direction).Normalize();
            root.LandingDistance *= s;
            for (int i = 0; i < root.BreakStartEndPointsPairs.Count; i++)
                root.BreakStartEndPointsPairs[i] = new ACadSharp.Objects.MultiLeaderObjectContextData.StartEndPointPair(m.Point(root.BreakStartEndPointsPairs[i].StartPoint), m.Point(root.BreakStartEndPointsPairs[i].EndPoint));
            foreach (var line in root.Lines)
            {
                for (int i = 0; i < line.Points.Count; i++) line.Points[i] = m.Point(line.Points[i]);
                for (int i = 0; i < line.StartEndPoints.Count; i++)
                    line.StartEndPoints[i] = new ACadSharp.Objects.MultiLeaderObjectContextData.StartEndPointPair(m.Point(line.StartEndPoints[i].StartPoint), m.Point(line.StartEndPoints[i].EndPoint));
            }
        }
        leader.ArrowheadSize *= s;
        leader.LandingDistance *= s;
    }

    private static string? RestoreSourcePath;
    private static CadDocument? RestoreDocument;

    private static bool FromOpened(JsonElement op) => op.TryGetProperty("fromOpened", out var value) && value.ValueKind == JsonValueKind.True;

    // An object an earlier save deleted is no longer in this DWG: it is cloned
    // from the drawing as the editor opened it (--restore-from).
    private static Entity FindRestoredEntity(JsonElement op)
    {
        if (RestoreSourcePath == null) throw new InvalidDataException("restore source is required for fromOpened");
        RestoreDocument ??= Read(RestoreSourcePath, new List<object>());
        return FindModelEntity(RestoreDocument, RequiredString(op, "copyOf"), op);
    }

    // The copy of a model-space entity, added to model space.  A dimension gets
    // its own copy of its block, as every dimension owns one.
    private static Entity CopyEntity(CadDocument document, Entity source)
    {
        var copy = (Entity)source.Clone();
        // Leader.Clone keeps the source's vertex list; the copy needs its own.
        if (source is Leader sourceLeader && copy is Leader copyLeader)
            copyLeader.Vertices = new List<XYZ>(sourceLeader.Vertices);
        if (source is Dimension sourceDimension && copy is Dimension copyDimension && sourceDimension.Block != null)
        {
            var block = new BlockRecord(NextAnonymousDimensionBlockName(document));
            foreach (var entity in sourceDimension.Block.Entities) block.Entities.Add((Entity)entity.Clone());
            document.BlockRecords.Add(block);
            copyDimension.Block = block;
        }
        document.ModelSpace.Entities.Add(copy);
        return copy;
    }

    private static string NextAnonymousDimensionBlockName(CadDocument document)
    {
        var used = new HashSet<string>(document.BlockRecords.Select(b => b.Name), StringComparer.OrdinalIgnoreCase);
        for (int i = 1; ; i++)
        {
            var name = "*D" + i.ToString(CultureInfo.InvariantCulture);
            if (!used.Contains(name)) return name;
        }
    }

    // Corners and boundaries are stored in the entity's own plane (OCS); a plan
    // move of a tilted one would need that transform, which is not done here.
    private static void RequirePlanNormal(XYZ normal, Entity entity)
    {
        if (Math.Abs(normal.X) > 1e-9 || Math.Abs(normal.Y) > 1e-9 || normal.Z < 0)
            throw new NotSupportedException($"Move is not supported for {entity.GetType().Name} outside the XY plane");
    }

    private static void MoveHatch(Hatch hatch, XYZ delta)
    {
        RequirePlanNormal(hatch.Normal, hatch);
        var d = new XY(delta.X, delta.Y);
        var d3 = new XYZ(delta.X, delta.Y, 0);
        foreach (var path in hatch.Paths)
        {
            foreach (var edge in path.Edges)
            {
                switch (edge)
                {
                    case Hatch.BoundaryPath.Line line: line.Start += d; line.End += d; break;
                    case Hatch.BoundaryPath.Arc arc: arc.Center += d; break;
                    case Hatch.BoundaryPath.Ellipse ellipseEdge: ellipseEdge.Center += d; break;
                    case Hatch.BoundaryPath.Spline splineEdge:
                        // Z of a spline edge control point is its weight.
                        for (int i = 0; i < splineEdge.ControlPoints.Count; i++) splineEdge.ControlPoints[i] += d3;
                        for (int i = 0; i < splineEdge.FitPoints.Count; i++) splineEdge.FitPoints[i] += d;
                        break;
                    case Hatch.BoundaryPath.Polyline polyline:
                        // Z of a polyline path vertex is its bulge.
                        for (int i = 0; i < polyline.Vertices.Count; i++) polyline.Vertices[i] += d3;
                        break;
                    default: throw new NotSupportedException($"Move is not supported for hatch edge {edge.GetType().Name}");
                }
            }
        }
        for (int i = 0; i < hatch.SeedPoints.Count; i++) hatch.SeedPoints[i] += d;
        // The pattern lines start at their base points; they move with the boundary.
        foreach (var line in hatch.Pattern.Lines) line.BasePoint += d;
    }

    // Every definition point moves, and so does the dimension's own block: that
    // block is what AutoCAD draws.
    private static void MoveDimension(Dimension dimension, XYZ delta)
    {
        foreach (var property in dimension.GetType().GetProperties())
        {
            if (property.PropertyType != typeof(XYZ) || !property.CanRead || !property.CanWrite || property.Name == nameof(Dimension.Normal)) continue;
            property.SetValue(dimension, (XYZ)property.GetValue(dimension)! + delta);
        }
        var block = dimension.Block;
        if (block == null) return;
        var document = dimension.Document;
        if (document != null && document.Entities.OfType<Dimension>().Count(d => ReferenceEquals(d.Block, block)) > 1)
            throw new NotSupportedException("Move is not supported for a dimension whose block other dimensions share");
        foreach (var entity in block.Entities.ToList()) MoveEntity(entity, delta);
    }

    private static void MoveMultiLeader(MultiLeader leader, XYZ delta)
    {
        var context = leader.ContextData;
        var oldBlockLocation = context.BlockContentLocation;
        context.ContentBasePoint += delta;
        context.TextLocation += delta;
        context.BlockContentLocation += delta;
        context.BasePoint += delta;
        foreach (var root in context.LeaderRoots)
        {
            root.ConnectionPoint += delta;
            ShiftPairs(root.BreakStartEndPointsPairs, delta);
            foreach (var line in root.Lines)
            {
                for (int i = 0; i < line.Points.Count; i++) line.Points[i] += delta;
                ShiftPairs(line.StartEndPoints, delta);
            }
        }
        if (context.HasContentsBlock)
        {
            // The block content transform carries the content location as its
            // translation; which triple holds it depends on how it was read.
            var m = context.TransformationMatrix;
            bool Near(double a, double b) => Math.Abs(a - b) <= 1e-6 * Math.Max(1, Math.Abs(b));
            if (Near(m.M03, oldBlockLocation.X) && Near(m.M13, oldBlockLocation.Y) && Near(m.M23, oldBlockLocation.Z))
            {
                m.M03 += delta.X; m.M13 += delta.Y; m.M23 += delta.Z;
            }
            else if (Near(m.M30, oldBlockLocation.X) && Near(m.M31, oldBlockLocation.Y) && Near(m.M32, oldBlockLocation.Z))
            {
                m.M30 += delta.X; m.M31 += delta.Y; m.M32 += delta.Z;
            }
            else throw new NotSupportedException("Move is not supported for a MULTILEADER whose block transform does not match its location");
            context.TransformationMatrix = m;
        }
    }

    private static void ShiftPairs(IList<ACadSharp.Objects.MultiLeaderObjectContextData.StartEndPointPair> pairs, XYZ delta)
    {
        for (int i = 0; i < pairs.Count; i++)
            pairs[i] = new ACadSharp.Objects.MultiLeaderObjectContextData.StartEndPointPair(pairs[i].StartPoint + delta, pairs[i].EndPoint + delta);
    }

    private static void UpdateEntity(CadDocument document, Entity entity, JsonElement op)
    {
        entity.Layer = ResolveLayer(document, op);
        ApplyEntityDisplayProperties(document, entity, op);
        switch (entity)
        {
            case Line line:
                if (op.TryGetProperty("start", out _)) line.StartPoint = ReadPoint(op, "start");
                if (op.TryGetProperty("end", out _)) line.EndPoint = ReadPoint(op, "end");
                break;
            // Arc derives from Circle, so it must be matched first.
            case Arc arc:
                if (op.TryGetProperty("center", out _)) arc.Center = ReadPoint(op, "center");
                if (op.TryGetProperty("radius", out _)) arc.Radius = ReadDouble(op, "radius", arc.Radius);
                if (op.TryGetProperty("startAngle", out _)) arc.StartAngle = ReadDouble(op, "startAngle", arc.StartAngle);
                if (op.TryGetProperty("endAngle", out _)) arc.EndAngle = ReadDouble(op, "endAngle", arc.EndAngle);
                break;
            case Circle circle:
                if (op.TryGetProperty("center", out _)) circle.Center = ReadPoint(op, "center");
                if (op.TryGetProperty("radius", out _)) circle.Radius = ReadDouble(op, "radius", circle.Radius);
                break;
            case LwPolyline poly:
                if (op.TryGetProperty("points", out var points))
                {
                    var vertices = points.EnumerateArray()
                        .Select(x => new LwPolyline.Vertex(ReadDouble(x, 0), ReadDouble(x, 1))).ToArray();
                    if (vertices.Length < 2) throw new InvalidDataException("update lwpolyline requires two points");
                    ApplyBulges(vertices, op, poly.Vertices.Select(x => x.Bulge).ToArray());
                    poly.Vertices.Clear();
                    foreach (var vertex in vertices) poly.Vertices.Add(vertex);
                }
                if (op.TryGetProperty("closed", out var closed)) poly.IsClosed = closed.ValueKind == JsonValueKind.True;
                break;
            case TextEntity text:
                if (op.TryGetProperty("text", out var value)) text.Value = value.GetString() ?? string.Empty;
                PlaceText(text, op.TryGetProperty("insert", out _) ? ReadPoint(op, "insert") : text.InsertPoint,
                    ReadDouble(op, "rotation", text.Rotation), ReadDouble(op, "height", text.Height));
                if (op.TryGetProperty("widthFactor", out _)) text.WidthFactor = ReadDouble(op, "widthFactor", text.WidthFactor);
                if (op.TryGetProperty("obliqueAngle", out _)) text.ObliqueAngle = ReadDouble(op, "obliqueAngle", text.ObliqueAngle);
                ApplyTextStyle(document, text, op);
                break;
            case MText mtext:
                if (op.TryGetProperty("text", out var mvalue)) mtext.Value = mvalue.GetString() ?? string.Empty;
                if (op.TryGetProperty("insert", out _)) mtext.InsertPoint = ReadPoint(op, "insert");
                if (op.TryGetProperty("height", out _)) mtext.Height = ReadDouble(op, "height", mtext.Height);
                // An MTEXT keeps its rotation as the direction of its x axis (DXF 11).
                if (op.TryGetProperty("rotation", out _))
                {
                    var turn = ReadDouble(op, "rotation", mtext.Rotation);
                    mtext.AlignmentPoint = new XYZ(Math.Cos(turn), Math.Sin(turn), 0);
                }
                ApplyTextStyle(document, mtext, op);
                break;
            case Insert insert:
            {
                var previous = insert.InsertPoint;
                double rotation = insert.Rotation, xs = insert.XScale, ys = insert.YScale, zs = insert.ZScale;
                if (op.TryGetProperty("rotation", out _)) rotation = ReadDouble(op, "rotation", insert.Rotation);
                if (op.TryGetProperty("scale", out var scale) && scale.ValueKind == JsonValueKind.Array)
                {
                    if (scale.GetArrayLength() > 0) xs = ReadDouble(scale, 0);
                    if (scale.GetArrayLength() > 1) ys = ReadDouble(scale, 1);
                    if (scale.GetArrayLength() > 2) zs = ReadDouble(scale, 2);
                }
                var turned = Math.Abs(rotation - insert.Rotation) > 1e-9 || Math.Abs(xs - insert.XScale) > 1e-9 ||
                             Math.Abs(ys - insert.YScale) > 1e-9 || Math.Abs(zs - insert.ZScale) > 1e-9;
                var placed = op.TryGetProperty("attributes", out var placedAttributes) && placedAttributes.ValueKind == JsonValueKind.Array;
                if (insert.Attributes.Any() && turned && !placed)
                    throw new NotSupportedException("rotating or scaling an INSERT with attributes is not supported");
                if (op.TryGetProperty("insert", out _)) insert.InsertPoint = ReadPoint(op, "insert");
                insert.Rotation = rotation;
                insert.XScale = xs;
                insert.YScale = ys;
                insert.ZScale = zs;
                // Attributes are separate entities placed in world space: they move
                // with the block, and go where the editor shows them (with their
                // edited values) when it sends them.
                if (insert.Attributes.Any() && placed) PlaceAttributes(insert, null, placedAttributes);
                else MoveAttributes(insert, insert.InsertPoint - previous);
                break;
            }
            case DimensionLinear linear:
                UpdateDimension(linear, document, op);
                break;
            case DimensionAligned aligned:
                UpdateDimension(aligned, document, op);
                break;
            default: throw new NotSupportedException($"Update is not supported for {entity.GetType().Name}");
        }
    }

    // Puts a TEXT (or ATTRIB) at the insertion point, rotation and height the
    // editor shows.  AutoCAD places justified text by its alignment point
    // (DXF 11), so that point keeps its place on the text: it turns and scales
    // with the text around the insertion point.
    private static void PlaceText(TextEntity text, XYZ insert, double rotation, double height)
    {
        if (text.HorizontalAlignment != TextHorizontalAlignment.Left ||
            text.VerticalAlignment != TextVerticalAlignmentType.Baseline)
        {
            var offset = text.AlignmentPoint - text.InsertPoint;
            var k = text.Height > 0 && height > 0 ? height / text.Height : 1;
            double c = Math.Cos(rotation - text.Rotation) * k, s = Math.Sin(rotation - text.Rotation) * k;
            text.AlignmentPoint = new XYZ(insert.X + c * offset.X - s * offset.Y, insert.Y + s * offset.X + c * offset.Y, insert.Z + offset.Z);
        }
        text.InsertPoint = insert;
        text.Rotation = rotation;
        text.Height = height;
    }

    // The attributes of a rotated, scaled or mirrored INSERT, where the editor
    // shows them ("attributes": insert, rotation, height).  Each is found by
    // its handle, for a copy by the handle of the source's attribute at the
    // same place, else by its tag when that is unique.  An attribute the
    // editor did not place is refused rather than left where it was.
    private static void PlaceAttributes(Insert insert, Insert? source, JsonElement placed)
    {
        var entries = placed.ValueKind == JsonValueKind.Array ? placed.EnumerateArray().ToList() : new List<JsonElement>();
        var attributes = insert.Attributes.ToList();
        var sourceAttributes = source?.Attributes.ToList();
        var used = new HashSet<int>();
        string EntryHandle(JsonElement entry, string name) =>
            entry.TryGetProperty(name, out var h) && h.ValueKind == JsonValueKind.String ? NormalizeHandle(h.GetString()) : "";
        for (var i = 0; i < attributes.Count; i++)
        {
            var attribute = attributes[i];
            var own = attribute.Handle != 0 ? NormalizeHandle(attribute.Handle.ToString("X")) : "";
            var from = sourceAttributes != null && i < sourceAttributes.Count ? NormalizeHandle(sourceAttributes[i].Handle.ToString("X")) : "";
            var found = Enumerable.Range(0, entries.Count).FirstOrDefault(j => !used.Contains(j) &&
                ((own.Length > 0 && EntryHandle(entries[j], "handle") == own) || (from.Length > 0 && EntryHandle(entries[j], "sourceHandle") == from)), -1);
            if (found < 0 && attributes.Count(a => string.Equals(a.Tag, attribute.Tag, StringComparison.OrdinalIgnoreCase)) == 1)
            {
                var tagged = Enumerable.Range(0, entries.Count).Where(j => !used.Contains(j) &&
                    entries[j].TryGetProperty("tag", out var tag) && string.Equals(tag.GetString(), attribute.Tag, StringComparison.OrdinalIgnoreCase)).ToList();
                if (tagged.Count == 1) found = tagged[0];
            }
            if (found < 0) throw new NotSupportedException("Transform is not supported for Insert (attributes not placed)");
            used.Add(found);
            var entry = entries[found];
            PlaceText(attribute, ReadPoint(entry, "insert"), ReadDouble(entry, "rotation", attribute.Rotation), ReadDouble(entry, "height", attribute.Height));
            // An edited value; a multiline attribute keeps its text in an MTEXT
            // the editor does not show, so its value is not changed here.
            if (entry.TryGetProperty("text", out var text) && text.ValueKind == JsonValueKind.String && text.GetString() != attribute.Value)
            {
                if (attribute.AttributeType != AttributeType.SingleLine)
                    throw new NotSupportedException("Update is not supported for AttributeEntity (multiline value)");
                attribute.Value = text.GetString() ?? string.Empty;
            }
        }
    }

    private static void MoveAttributes(Insert insert, XYZ delta)
    {
        foreach (var attribute in insert.Attributes)
        {
            attribute.InsertPoint += delta;
            attribute.AlignmentPoint += delta;
        }
    }

    // A copy ("copyOf") clones the source INSERT so attribute values and XData stay;
    // otherwise a fresh INSERT of the named block is created.
    private static Insert CreateInsert(CadDocument document, JsonElement op)
    {
        var copyOf = op.TryGetProperty("copyOf", out var copyProperty) ? copyProperty.GetString() : null;
        var fresh = string.IsNullOrEmpty(copyOf);
        Insert insert;
        if (!fresh)
        {
            var source = (FromOpened(op) ? FindRestoredEntity(op) : FindModelEntity(document, copyOf)) as Insert
                ?? throw new InvalidDataException($"copy source is not an INSERT: {copyOf}");
            insert = (Insert)source.Clone();
        }
        else
        {
            var name = RequiredString(op, "blockName");
            var block = document.BlockRecords.FirstOrDefault(x => string.Equals(x.Name, name, StringComparison.OrdinalIgnoreCase))
                ?? throw new InvalidDataException($"block not found: {name}");
            insert = new Insert(block);
        }
        var previous = insert.InsertPoint;
        var rotation = ReadDouble(op, "rotation", insert.Rotation);
        double xs = insert.XScale, ys = insert.YScale, zs = insert.ZScale;
        if (op.TryGetProperty("scale", out var scale) && scale.ValueKind == JsonValueKind.Array)
        {
            if (scale.GetArrayLength() > 0) xs = ReadDouble(scale, 0);
            if (scale.GetArrayLength() > 1) ys = ReadDouble(scale, 1);
            if (scale.GetArrayLength() > 2) zs = ReadDouble(scale, 2);
        }
        var turned = Math.Abs(rotation - insert.Rotation) > 1e-9 || Math.Abs(xs - insert.XScale) > 1e-9 ||
                     Math.Abs(ys - insert.YScale) > 1e-9 || Math.Abs(zs - insert.ZScale) > 1e-9;
        var placed = op.TryGetProperty("attributes", out var placedAttributes) && placedAttributes.ValueKind == JsonValueKind.Array;
        if (!fresh && insert.Attributes.Any() && turned && !placed)
            throw new NotSupportedException("rotating or scaling a copied INSERT with attributes is not supported");
        insert.InsertPoint = ReadPoint(op, "insert");
        insert.Rotation = rotation;
        insert.XScale = xs;
        insert.YScale = ys;
        insert.ZScale = zs;
        if (fresh)
        {
            // new Insert(block) builds the attributes at the origin; place them with the insert.
            var transform = insert.GetTransform();
            foreach (var attribute in insert.Attributes) attribute.ApplyTransform(transform);
            return insert;
        }
        if (insert.Attributes.Any() && placed) PlaceAttributes(insert, (Insert)(FromOpened(op) ? FindRestoredEntity(op) : FindModelEntity(document, copyOf!)), placedAttributes);
        else MoveAttributes(insert, insert.InsertPoint - previous);
        return insert;
    }

    private static Dimension CreateDimension(CadDocument document, JsonElement op)
    {
        var kind = op.TryGetProperty("dimensionKind", out var k) ? (k.GetString() ?? "aligned") : "aligned";
        Dimension dimension = string.Equals(kind, "linear", StringComparison.OrdinalIgnoreCase)
            ? new DimensionLinear()
            : new DimensionAligned();
        UpdateDimension((DimensionAligned)dimension, document, op);
        return dimension;
    }

    private static void UpdateDimension(DimensionAligned dimension, CadDocument document, JsonElement op)
    {
        dimension.FirstPoint = ReadPoint(op, "firstPoint");
        dimension.SecondPoint = ReadPoint(op, "secondPoint");
        dimension.DefinitionPoint = ReadPoint(op, "definitionPoint");
        if (op.TryGetProperty("textPosition", out _))
        {
            dimension.TextMiddlePoint = ReadPoint(op, "textPosition");
            dimension.IsTextUserDefinedLocation = true;
        }
        if (op.TryGetProperty("overrideText", out var text) && text.ValueKind == JsonValueKind.String)
            dimension.Text = text.GetString() ?? string.Empty;
        if (dimension is DimensionLinear linear && op.TryGetProperty("rotation", out _))
            linear.Rotation = ReadDouble(op, "rotation", 0);
        if (dimension is DimensionAligned aligned && dimension is not DimensionLinear && op.TryGetProperty("rotation", out _))
            aligned.ExtLineRotation = ReadDouble(op, "rotation", 0);
        dimension.Layer = ResolveLayer(document, op);
        dimension.Style = ResolveDimensionStyle(document, op);
    }

    private static DimensionStyle ResolveDimensionStyle(CadDocument document, JsonElement op)
    {
        var name = SafeName(op.TryGetProperty("dimensionStyle", out var styleName) ? styleName.GetString() ?? "CBL_DIMSTYLE" : "CBL_DIMSTYLE");
        var style = document.DimensionStyles.FirstOrDefault(x => string.Equals(x.Name, name, StringComparison.OrdinalIgnoreCase));
        if (style == null)
        {
            style = new DimensionStyle(name);
            document.DimensionStyles.Add(style);
        }
        style.ArrowSize = Math.Max(0, ReadDouble(op, "arrowSize", 11));
        style.TextHeight = Math.Max(0.0001, ReadDouble(op, "textHeight", 20));
        style.DimensionLineColor = new Color((short)ReadInt(op, "lineColor", 7));
        style.ExtensionLineColor = new Color((short)ReadInt(op, "extensionColor", 7));
        style.TextColor = new Color((short)ReadInt(op, "textColor", 3));
        if (op.TryGetProperty("textStyle", out var textStyleValue) && textStyleValue.ValueKind == JsonValueKind.String)
        {
            var textStyleName = textStyleValue.GetString();
            var textStyle = document.TextStyles.FirstOrDefault(x => string.Equals(x.Name, textStyleName, StringComparison.OrdinalIgnoreCase));
            if (textStyle != null) style.Style = textStyle;
        }
        return style;
    }

    private static void ApplyTextStyle(CadDocument? document, Entity entity, JsonElement op)
    {
        if (document == null || !op.TryGetProperty("textStyle", out var styleValue)) return;
        var name = styleValue.GetString();
        if (string.IsNullOrWhiteSpace(name)) return;
        var style = document.TextStyles.FirstOrDefault(x => string.Equals(x.Name, name, StringComparison.OrdinalIgnoreCase));
        if (style == null) return;
        switch (entity)
        {
            case TextEntity text: text.Style = style; break;
            case MText mtext: mtext.Style = style; break;
        }
    }

    private static void SyncTextStyle(CadDocument document, JsonElement op)
    {
        var rawHandle = op.TryGetProperty("handle", out var h) ? h.GetString() : null;
        if (string.IsNullOrWhiteSpace(rawHandle)) return;
        TextStyle? style = null;
        if (!string.IsNullOrWhiteSpace(rawHandle) && ulong.TryParse(rawHandle.Replace("0x", "", StringComparison.OrdinalIgnoreCase), NumberStyles.HexNumber, CultureInfo.InvariantCulture, out var handle))
            style = document.TextStyles.FirstOrDefault(x => x.Handle == handle);
        style ??= document.TextStyles.FirstOrDefault(x => string.Equals(x.Name, op.TryGetProperty("name", out var n) ? n.GetString() : null, StringComparison.OrdinalIgnoreCase));
        if (style == null) return;
        if (op.TryGetProperty("name", out var name) && !string.IsNullOrWhiteSpace(name.GetString()) && !string.Equals(name.GetString(), TextStyle.DefaultName, StringComparison.OrdinalIgnoreCase) && !string.Equals(style.Name, name.GetString(), StringComparison.Ordinal))
            style.Name = name.GetString()!;
        if (op.TryGetProperty("fontFile", out var font)) style.Filename = font.GetString() ?? string.Empty;
        if (op.TryGetProperty("bigFontFile", out var big)) style.BigFontFilename = big.GetString() ?? string.Empty;
        if (op.TryGetProperty("fixedHeight", out var height)) style.Height = height.GetDouble();
        if (op.TryGetProperty("widthFactor", out var width)) style.Width = width.GetDouble();
        if (op.TryGetProperty("obliqueAngle", out var oblique)) style.ObliqueAngle = oblique.GetDouble();
        if (op.TryGetProperty("flags", out var flags)) style.Flags = (StyleFlags)flags.GetInt32();
    }

    private static Layer ResolveLayer(CadDocument document, JsonElement op)
    {
        var name = SafeName(op.TryGetProperty("layer", out var layer) ? layer.GetString() ?? "0" : "0");
        var result = document.Layers.FirstOrDefault(x => x.Name == name);
        if (result != null) return result;
        var aci = ReadInt(op, "color", 7);
        // 0/256 are entity BYBLOCK/BYLAYER values, not valid layer colors.
        // A newly created layer needs a concrete ACI color.
        if (aci <= 0 || aci >= 256) aci = 7;
        result = new Layer(name) { Color = new Color((short)aci) };
        document.Layers.Add(result);
        return result;
    }

    private static string SafeName(string value) => string.IsNullOrWhiteSpace(value) ? "CBL_LOCAL_LAYER" : value.Trim()[..Math.Min(255, value.Trim().Length)];

    private static void ApplyEntityDisplayProperties(CadDocument document, Entity entity, JsonElement op)
    {
        if (op.TryGetProperty("trueColor", out var trueColor) &&
            trueColor.ValueKind == JsonValueKind.Number && trueColor.TryGetUInt32(out var rgb))
        {
            entity.Color = ColorFromRgb(rgb);
        }
        else if (op.TryGetProperty("aci", out _) || op.TryGetProperty("color", out _))
        {
            entity.Color = new Color((short)ReadInt(op, op.TryGetProperty("aci", out _) ? "aci" : "color", 256));
        }

        if (op.TryGetProperty("linetype", out var lineTypeValue) && lineTypeValue.ValueKind == JsonValueKind.String)
        {
            var name = CanonicalLineTypeName(lineTypeValue.GetString());
            if (!string.IsNullOrWhiteSpace(name))
            {
                var lineType = document.LineTypes.FirstOrDefault(x => string.Equals(x.Name, name, StringComparison.OrdinalIgnoreCase));
                if (lineType == null) throw new InvalidDataException($"Linetype not found: {name}");
                entity.LineType = lineType;
            }
        }

        if (op.TryGetProperty("lineweight", out var lineWeightValue))
        {
            var raw = lineWeightValue.ValueKind == JsonValueKind.String ? lineWeightValue.GetString() ?? string.Empty : lineWeightValue.ToString();
            if (Enum.TryParse<LineWeightType>(raw, true, out var parsed)) entity.LineWeight = parsed;
            else if (int.TryParse(raw, NumberStyles.Integer, CultureInfo.InvariantCulture, out var numeric)) entity.LineWeight = (LineWeightType)numeric;
        }
    }

    // Ops carry true colour like DXF group 420 (0xRRGGBB); ACadSharp's
    // Color.FromTrueColor takes its own little-endian 0xBBGGRR value.
    private static Color ColorFromRgb(uint rgb) =>
        new Color((byte)((rgb >> 16) & 0xFF), (byte)((rgb >> 8) & 0xFF), (byte)(rgb & 0xFF));

    // Metadata reports true colour in the same 0xRRGGBB order, because the
    // editor shows it and sends it back unchanged in update ops.
    private static int? RgbOf(Color color)
    {
        if (!color.IsTrueColor) return null;
        var rgb = color.GetTrueColorRgb();
        return rgb[0] << 16 | rgb[1] << 8 | rgb[2];
    }

    private static string CanonicalLineTypeName(string? raw)
    {
        var value = (raw ?? string.Empty).Trim();
        var key = new string(value.ToUpperInvariant().Where(char.IsLetterOrDigit).ToArray());
        return key switch
        {
            "SOLID" or "CONTINUOUS" or "CONTINUE" or "실선" => "Continuous",
            "BYLAYER" or "LAYER" => "ByLayer",
            "BYBLOCK" or "BLOCK" => "ByBlock",
            _ => value,
        };
    }

    private static string RequiredString(JsonElement obj, string name) => obj.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.String && !string.IsNullOrWhiteSpace(value.GetString()) ? value.GetString()! : throw new InvalidDataException($"{name} is required");
    private static bool ReadBool(JsonElement obj, string name) => obj.TryGetProperty(name, out var value) && value.ValueKind == JsonValueKind.True;
    private static int ReadInt(JsonElement obj, string name, int fallback) => obj.TryGetProperty(name, out var value) && value.TryGetInt32(out var result) ? result : fallback;
    private static double ReadDouble(JsonElement obj, string name, double fallback) => obj.TryGetProperty(name, out var value) && value.TryGetDouble(out var result) && double.IsFinite(result) ? result : fallback;
    private static double ReadDouble(JsonElement value, int index) => value.ValueKind == JsonValueKind.Array && value.GetArrayLength() > index && value[index].TryGetDouble(out var result) ? result : throw new InvalidDataException("invalid point");
    private static XYZ ReadPoint(JsonElement obj, string name) { var value = obj.GetProperty(name); return new XYZ(ReadDouble(value, 0), ReadDouble(value, 1), value.ValueKind == JsonValueKind.Array && value.GetArrayLength() > 2 ? ReadDouble(value, 2) : 0); }

    // "bulges" holds one bulge per vertex (arc segment to the next vertex).  Clients
    // that send only points keep the entity's existing bulges when the vertex count
    // is unchanged, so an update never flattens arc segments it did not mention.
    private static void ApplyBulges(LwPolyline.Vertex[] vertices, JsonElement op, double[]? existing)
    {
        if (op.TryGetProperty("bulges", out var bulges) && bulges.ValueKind == JsonValueKind.Array)
        {
            if (bulges.GetArrayLength() != vertices.Length)
                throw new InvalidDataException("lwpolyline bulges must match the point count");
            for (var i = 0; i < vertices.Length; i++)
            {
                var bulge = ReadDouble(bulges, i);
                if (!double.IsFinite(bulge)) throw new InvalidDataException("invalid lwpolyline bulge");
                vertices[i].Bulge = bulge;
            }
            return;
        }
        if (existing != null && existing.Length == vertices.Length)
            for (var i = 0; i < vertices.Length; i++) vertices[i].Bulge = existing[i];
    }

    private static CadDocument Read(string path, List<object> notifications)
    {
        var readNotifications = new List<object>();
        var document = ReadOnce(path, readNotifications, false);
        if (DwgReader.MisdeclaredUtf8Strings > 0)
        {
            // Hangul strings stored as UTF-8 under the drawing's code page show
            // that an older pipeline saved it (acadsharp-misdeclared-utf8.patch).
            // Read it again taking every valid UTF-8 string as UTF-8, so
            // "900×400" does not come out as "900횞400".  A save writes them in
            // the code page; the open API reports the count.
            readNotifications.Clear();
            document = ReadOnce(path, readNotifications, true);
        }
        notifications.AddRange(readNotifications);
        if (DwgReader.MisdeclaredUtf8Strings > 0)
            notifications.Add(new { phase = "read", type = "Warning", Message = $"Misdeclared UTF-8 strings read: {DwgReader.MisdeclaredUtf8Strings}", exception = (string?)null });
        // Some converters write a code page index ACadSharp does not list
        // (45); it reads such strings as UTF-8 and leaves CodePage null, so
        // the DXF (open) and DWG (save) writers threw.  Use the Korean code
        // page, as for new drawings; \U+XXXX covers anything outside it.
        if (string.IsNullOrWhiteSpace(document.Header.CodePage)) document.Header.CodePage = "kcs5601";
        return document;
    }

    // R2013+ drawings keep REGION/3DSOLID/BODY ACIS data in the AcDs data
    // section; ACadSharp reads that section but leaves the entities without
    // it, so the AC1018 writer refused ("has no ACIS payload").  Give each
    // entity its stored SAB, which the patched writer stores as version 2
    // (acadsharp-region-sab.patch).  Data in any other form is not attached,
    // so such a drawing is still refused rather than written wrong.
    private static int AttachStoredAcis(CadDocument document)
    {
        if (document.DataStorage == null) return 0;
        static bool IsSab(byte[] data) => data.AsSpan().StartsWith("ACIS BinaryFile"u8) || data.AsSpan().StartsWith("ASM BinaryFile"u8);
        var attached = 0;
        foreach (var geometry in document.ModelSpace.Entities.OfType<ModelerGeometry>()
                     .Concat(document.BlockRecords.SelectMany(block => block.Entities.OfType<ModelerGeometry>())).Distinct())
        {
            if (geometry.AcisData != null && geometry.AcisData.Length > 0) continue;
            if (document.DataStorage.TryGetDataByHandle(geometry.Handle, out var data) && data != null && IsSab(data))
            {
                geometry.AcisData = data;
                attached++;
            }
        }
        return attached;
    }

    private static CadDocument ReadOnce(string path, List<object> notifications, bool preferUtf8)
    {
        NotificationEventHandler callback = (_, e) => notifications.Add(new { phase = "read", type = e.NotificationType.ToString(), e.Message, exception = e.Exception?.ToString() });
        DwgReader.ResetMisdeclaredUtf8Strings();
        DwgReader.PreferMisdeclaredUtf8 = preferUtf8;
        try { return DwgReader.Read(path, callback); }
        finally { DwgReader.PreferMisdeclaredUtf8 = false; }
    }

    private static FileStream AcquireLock(string path, TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (true)
        {
            try { return new FileStream(path, FileMode.OpenOrCreate, FileAccess.ReadWrite, FileShare.None); }
            catch (IOException) when (DateTime.UtcNow < deadline) { Thread.Sleep(250); }
        }
    }

    private static bool TryParseVersion(string name, out ACadVersion version)
    {
        version = name.ToUpperInvariant() switch
        {
            "AC1018" or "AC2004" => ACadVersion.AC1018,
            _ => ACadVersion.Unknown
        };
        return version != ACadVersion.Unknown;
    }

    private static string Sha256(string path)
    {
        using var stream = File.OpenRead(path);
        return Convert.ToHexString(SHA256.HashData(stream)).ToLowerInvariant();
    }

    private static SnapshotData Snapshot(CadDocument document)
    {
        var counts = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
        var texts = new List<string>();
        var regions = new List<object>();
        var model = 0;
        foreach (var entity in document.ModelSpace.Entities) { Add(entity, counts, texts, regions); model++; }
        var blocks = 0;
        foreach (var block in document.BlockRecords)
        {
            if (block.Name.StartsWith("*Model", StringComparison.OrdinalIgnoreCase) || block.Name.StartsWith("*Paper", StringComparison.OrdinalIgnoreCase)) continue;
            blocks++;
            foreach (var entity in block.Entities) Add(entity, counts, texts, regions);
        }
        // Keep the same semantic fields for TEXT and MTEXT that the metadata
        // manifest uses.  The old abbreviated projection only emitted text
        // and type, which made a reader-side TEXT/MTEXT representation change
        // look like a source text loss and could not validate position,
        // rotation, or height.
        var modelSpaceRecords = new List<object>();
        AddMetadata(document.ModelSpace.Entities, "ModelSpace", modelSpaceRecords);
        var layerRecords = document.Layers
            .Select(x => new { name = x.Name, handle = x.Handle.ToString("X"), owner = x.Owner?.Handle.ToString("X") })
            .OrderBy(x => x.name, StringComparer.Ordinal)
            .ToArray();
        var textStyles = document.TextStyles
            .Select(x => new { name = x.Name, filename = x.Filename, bigFontFilename = x.BigFontFilename })
            .OrderBy(x => x.name, StringComparer.Ordinal)
            .ToArray();
        return new SnapshotData(model, blocks, counts, texts.Distinct(StringComparer.Ordinal).OrderBy(x => x, StringComparer.Ordinal).ToArray(), layerRecords.Select(x => x.name).ToArray(), layerRecords, textStyles, regions, modelSpaceRecords.ToArray());
    }

    private static void Add(Entity entity, Dictionary<string, int> counts, List<string> texts, List<object> regions)
    {
        var name = entity.GetType().Name;
        counts[name] = counts.TryGetValue(name, out var n) ? n + 1 : 1;
        if (entity is TextEntity text) texts.Add(text.Value ?? string.Empty);
        if (entity is MText mtext) texts.Add(mtext.Value ?? string.Empty);
        if (entity is Region region) regions.Add(new { handle = region.Handle.ToString("X"), rawBytes = region.RawAcisData?.Length ?? 0, rawSha256 = region.RawAcisData is null ? null : Convert.ToHexString(SHA256.HashData(region.RawAcisData)).ToLowerInvariant(), dataBytes = region.AcisData?.Length ?? 0, dataSha256 = region.AcisData is null ? null : Convert.ToHexString(SHA256.HashData(region.AcisData)).ToLowerInvariant(), blockSizes = region.RawAcisBlocks.Select(x => x.Length).ToArray() });
    }

    private static int Fail(string message) { Console.Error.WriteLine(message); return 2; }
    private static void TryDelete(string path) { try { if (File.Exists(path)) File.Delete(path); } catch { } }

    private sealed record SnapshotData(int ModelSpace, int BlockDefinitions, Dictionary<string, int> Counts, string[] Texts, string[] Layers, object[] LayerRecords, object[] TextStyles, List<object> Regions, object[] ModelSpaceEntities)
    {
        public int EntityTotal => Counts.Values.Sum();
    }
}
