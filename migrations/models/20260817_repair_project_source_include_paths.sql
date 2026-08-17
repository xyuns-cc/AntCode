-- 修复 project_sources.include_paths 的历史脏数据。
--
-- 成因：前端把 include_paths 整体 JSON.stringify 成**一个**表单值，而后端把该字段
-- 声明为 list[str] 交给 Starlette 按「重复同名键」收集，于是整段 JSON 文本被当成
-- 列表的唯一元素落库：`[]` 存成 `["[]"]`、`["libs"]` 存成 `["[\"libs\"]"]`。
-- 随后 source bundle 会对着名为 `[]` 的目录做 resolve_existing_dir 并抛
-- FileNotFoundError —— UI 建的每个 Git 文件/代码项目都无法执行任务。
--
-- 线格式已改为「每个路径各占一个同名表单条目」，见
-- contracts/http/project_create_form.json 与两侧的契约测试。本脚本只负责把
-- 已经落库的旧值还原成它本来该是的数组。可重复执行。
--
-- run_source_snapshots.include_paths 不需要修：快照在 source bundle 打包成功之后
-- 才写入，而受影响的项目在打包阶段就失败了，脏值到不了快照表。

DO $$
DECLARE
    repaired_count INTEGER;
    unrepaired_count INTEGER;
BEGIN
    -- 只认唯一已知的损坏形态：数组恰好一个元素，该元素是一段可解析为「字符串数组」
    -- 的 JSON 文本。其余形态一律不猜，交给下面的守卫报错。
    WITH corrupted AS (
        SELECT source.id,
               ((source.include_paths ->> 0)::jsonb) AS decoded
          FROM public.project_sources AS source
         WHERE jsonb_typeof(source.include_paths) = 'array'
           AND jsonb_array_length(source.include_paths) = 1
           AND jsonb_typeof(source.include_paths -> 0) = 'string'
           AND pg_input_is_valid(source.include_paths ->> 0, 'jsonb')
           AND jsonb_typeof((source.include_paths ->> 0)::jsonb) = 'array'
           AND NOT EXISTS (
               SELECT 1
                 FROM jsonb_array_elements((source.include_paths ->> 0)::jsonb) AS decoded_item
                WHERE jsonb_typeof(decoded_item) <> 'string'
           )
    )
    UPDATE public.project_sources AS target
       SET include_paths = corrupted.decoded,
           updated_at = NOW()
      FROM corrupted
     WHERE target.id = corrupted.id;

    GET DIAGNOSTICS repaired_count = ROW_COUNT;
    RAISE NOTICE 'project_sources.include_paths 已修复 % 行', repaired_count;

    -- 守卫：任何仍然长得像「JSON 数组文本被当成路径」的残留都必须让人看见，
    -- 不能静默留在库里等到派发任务时才炸。
    SELECT count(*)
      INTO unrepaired_count
      FROM public.project_sources AS source,
           LATERAL jsonb_array_elements_text(source.include_paths) AS path_item
     WHERE jsonb_typeof(source.include_paths) = 'array'
       AND path_item ~ '^\s*\[.*\]\s*$';

    IF unrepaired_count > 0 THEN
        RAISE EXCEPTION
            'project_sources.include_paths 仍有 % 个形似 JSON 数组文本的路径无法自动还原，请人工核对后再上线',
            unrepaired_count;
    END IF;
END $$;
