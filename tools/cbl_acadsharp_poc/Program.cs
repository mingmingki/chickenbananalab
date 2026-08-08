using System.Diagnostics;
using System.Globalization;
using System.Security.Cryptography;
using System.Text.Json;
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
        if (args.Length == 3 && string.Equals(args[0], "--dxf", StringComparison.OrdinalIgnoreCase))
            return WriteDxfFromDwg(args[1], args[2]);
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
            var document = Read(input, notifications);
            document.Header.Version = version;
            var editReport = opsPath != null
                ? ApplyOperations(document, opsPath)
                : edit ? ApplyEdits(document) : null;
            var before = Snapshot(document);

            using (var writer = new DwgWriter(temp, document))
            {
                writer.Configuration.CloseStream = true;
                writer.OnNotification += (_, e) => notifications.Add(new { phase = "write", type = e.NotificationType.ToString(), e.Message, exception = e.Exception?.ToString() });
                writer.Write();
            }

            if (!File.Exists(temp) || new FileInfo(temp).Length < 1024) throw new InvalidDataException("writer produced an empty or implausibly small DWG");
            var rereadNotifications = new List<object>();
            var reread = Read(temp, rereadNotifications);
            var after = Snapshot(reread);
            if (after.EntityTotal == 0 && before.EntityTotal > 0) throw new InvalidDataException("writer reread has no entities");
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
                sourceHeaderCodePage = document.Header.CodePage,
                rereadHeaderCodePage = reread.Header.CodePage,
                source = before,
                reread = after,
                notifications,
                rereadNotifications,
                editReport,
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

    private static int CreateNew(string outputPath, string versionName, string? opsPath)
    {
        var output = Path.GetFullPath(outputPath);
        if (!TryParseVersion(versionName, out var version)) return Fail($"unsupported target version: {versionName}");
        Directory.CreateDirectory(Path.GetDirectoryName(output)!);
        var temp = output + $".tmp.{Environment.ProcessId}.{Guid.NewGuid():N}";
        try
        {
            var document = new CadDocument(version);
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
                type.Equals("add_lwpolyline", StringComparison.OrdinalIgnoreCase) ||
                type.Equals("add_text", StringComparison.OrdinalIgnoreCase) ||
                type.Equals("add_mtext", StringComparison.OrdinalIgnoreCase))
                return true;
        }
        return false;
    }

    private static int WriteDxfFromDwg(string inputPath, string outputPath)
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
            DxfWriter.Write(temp, document, false, notification: (_, e) => notifications.Add(new
            {
                phase = "write-dxf", type = e.NotificationType.ToString(), e.Message,
                exception = e.Exception?.ToString()
            }));
            if (!File.Exists(temp) || new FileInfo(temp).Length < 1024)
                throw new InvalidDataException("DxfWriter produced an empty or implausibly small DXF");
            File.Move(temp, output, true);
            var rereadNotifications = new List<object>();
            var reread = DxfReader.Read(output, (_, e) => rereadNotifications.Add(new
            {
                phase = "read-dxf", type = e.NotificationType.ToString(), e.Message,
                exception = e.Exception?.ToString()
            }));
            var report = new
            {
                input, output, sourceSha256 = Sha256(input), outputSha256 = Sha256(output),
                sourceBytes = new FileInfo(input).Length, outputBytes = new FileInfo(output).Length,
                elapsedMs = sw.Elapsed.TotalMilliseconds, source = Snapshot(document),
                reread = Snapshot(reread), notifications, rereadNotifications,
                status = "dxf_written_and_reread"
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

    private static int WriteMetadata(string inputPath)
    {
        var input = Path.GetFullPath(inputPath);
        if (!File.Exists(input)) return Fail($"input does not exist: {input}");
        var notifications = new List<object>();
        try
        {
            var document = Read(input, notifications);
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
                    owner = layer.Owner == null ? null : Hex(layer.Owner.Handle)
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
            Console.WriteLine(JsonSerializer.Serialize(result, new JsonSerializerOptions { WriteIndented = true }));
            return 0;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine(JsonSerializer.Serialize(new { input, mode = "metadata", status = "failed", error = ex.ToString() }, new JsonSerializerOptions { WriteIndented = true }));
            return 1;
        }
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
                ["trueColor"] = entity.Color.IsTrueColor ? entity.Color.TrueColor : null,
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
                case "add_lwpolyline":
                {
                    var points = op.GetProperty("points").EnumerateArray()
                        .Select(x => new LwPolyline.Vertex(ReadDouble(x, 0), ReadDouble(x, 1))).ToArray();
                    if (points.Length < 2) throw new InvalidDataException("add_lwpolyline requires two points");
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
            case Insert insert: insert.InsertPoint += delta; break;
            default: throw new NotSupportedException($"Move is not supported for {entity.GetType().Name}");
        }
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
                    poly.Vertices.Clear();
                    foreach (var vertex in vertices) poly.Vertices.Add(vertex);
                }
                if (op.TryGetProperty("closed", out var closed)) poly.IsClosed = closed.ValueKind == JsonValueKind.True;
                break;
            case TextEntity text:
                if (op.TryGetProperty("text", out var value)) text.Value = value.GetString() ?? string.Empty;
                if (op.TryGetProperty("insert", out _)) text.InsertPoint = ReadPoint(op, "insert");
                if (op.TryGetProperty("height", out _)) text.Height = ReadDouble(op, "height", text.Height);
                if (op.TryGetProperty("rotation", out _)) text.Rotation = ReadDouble(op, "rotation", text.Rotation);
                if (op.TryGetProperty("widthFactor", out _)) text.WidthFactor = ReadDouble(op, "widthFactor", text.WidthFactor);
                if (op.TryGetProperty("obliqueAngle", out _)) text.ObliqueAngle = ReadDouble(op, "obliqueAngle", text.ObliqueAngle);
                ApplyTextStyle(document, text, op);
                break;
            case MText mtext:
                if (op.TryGetProperty("text", out var mvalue)) mtext.Value = mvalue.GetString() ?? string.Empty;
                if (op.TryGetProperty("insert", out _)) mtext.InsertPoint = ReadPoint(op, "insert");
                if (op.TryGetProperty("height", out _)) mtext.Height = ReadDouble(op, "height", mtext.Height);
                ApplyTextStyle(document, mtext, op);
                break;
            case Insert insert:
                if (op.TryGetProperty("insert", out _)) insert.InsertPoint = ReadPoint(op, "insert");
                if (op.TryGetProperty("rotation", out _)) insert.Rotation = ReadDouble(op, "rotation", insert.Rotation);
                if (op.TryGetProperty("scale", out var scale) && scale.ValueKind == JsonValueKind.Array)
                {
                    if (scale.GetArrayLength() > 0) insert.XScale = ReadDouble(scale, 0);
                    if (scale.GetArrayLength() > 1) insert.YScale = ReadDouble(scale, 1);
                    if (scale.GetArrayLength() > 2) insert.ZScale = ReadDouble(scale, 2);
                }
                break;
            case DimensionLinear linear:
                UpdateDimension(linear, document, op);
                break;
            case DimensionAligned aligned:
                UpdateDimension(aligned, document, op);
                break;
            default: throw new NotSupportedException($"Update is not supported for {entity.GetType().Name}");
        }
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
            entity.Color = Color.FromTrueColor(rgb);
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

    private static CadDocument Read(string path, List<object> notifications)
    {
        NotificationEventHandler callback = (_, e) => notifications.Add(new { phase = "read", type = e.NotificationType.ToString(), e.Message, exception = e.Exception?.ToString() });
        return DwgReader.Read(path, callback);
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
        var modelSpaceEntities = document.ModelSpace.Entities
            .Select(entity => new
            {
                handle = entity.Handle.ToString("X"),
                entity = entity.GetType().Name,
                text = entity is TextEntity textEntity ? textEntity.Value : entity is MText mtextEntity ? mtextEntity.Value : null
            })
            .ToArray();
        var layerRecords = document.Layers
            .Select(x => new { name = x.Name, handle = x.Handle.ToString("X"), owner = x.Owner?.Handle.ToString("X") })
            .OrderBy(x => x.name, StringComparer.Ordinal)
            .ToArray();
        var textStyles = document.TextStyles
            .Select(x => new { name = x.Name, filename = x.Filename, bigFontFilename = x.BigFontFilename })
            .OrderBy(x => x.name, StringComparer.Ordinal)
            .ToArray();
        return new SnapshotData(model, blocks, counts, texts.Distinct(StringComparer.Ordinal).OrderBy(x => x, StringComparer.Ordinal).ToArray(), layerRecords.Select(x => x.name).ToArray(), layerRecords, textStyles, regions, modelSpaceEntities);
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
