# ESJZone EPUB 重构功能设计方案

> 版本：v1.0
> 日期：2026-08-01
> 目标：提供一套**严格遵循 Sigil / EPUB3 规范**的重构工具，可对任意已生成的 EPUB 进行格式校正、结构优化与风格统一，无需重新下载图片或正文。

---

## 一、背景与问题

### 1.1 现状
- `core/epub.py` 使用 `ebooklib` 组装 EPUB，已修复段首缩进双倍叠加、章节标题上边距、标签胶囊等问题
- 但生成产物在 **Sigil** 中打开时仍有 CSS 警告（如 `body[epub\:type="titlepage"]` 转义冒号）
- 已生成的历史 EPUB 无法直接受益于代码更新，需要重新跑完整下载任务才能生效

### 1.2 痛点
1. **重新生成成本高**：119 章 + 图片下载耗时数分钟到十几分钟
2. **Sigil 兼容性**：CSS 转义选择器、XHTML 格式化、命名空间声明等细节点易触发编辑器警告
3. **历史 EPUB 无法原地升级**：修改只影响新生成，存量文件需手动处理

---

## 二、重构功能设计目标

| 目标 | 说明 |
|------|------|
| **Sigil 零警告** | CSS / XHTML / OPF 严格符合 EPUB3 规范，Sigil 打开无 ERROR/WARNING |
| **原地修改** | 不重新下载图片/正文，仅调整 CSS、XHTML 结构、OPF 元数据 |
| **幂等可重入** | 对同一文件多次运行结果一致，不产生重复 class 或无效标记 |
| **备份安全** | 修改前自动备份原文件（`.bak`），失败时回滚 |
| **批量支持** | 支持单文件或 `novels/` 目录下批量重构 |

---

## 三、重构范围与检查清单

### 3.1 CSS 层（`Styles/stylesheet.css`）
- [ ] 移除 `epub\:type` 属性选择器的转义冒号 → 改用 `.title-page-body` / `.toc-page-nav` class
- [ ] 确认花括号配对、属性名合法（无 `-webkit-` 等非标准但保留）
- [ ] 确认 `@namespace` 声明存在（如 `epub` 前缀）
- [ ] 移动端媒体查询 `@media (min-width: 768px)` 语法正确

### 3.2 XHTML 层
- [ ] `title.xhtml`：`<body>` 含 `class="title-page-body"` + `epub:type="titlepage"`
- [ ] 所有 XHTML 文件含 `<?xml version="1.0" encoding="UTF-8"?>` 声明（可选但推荐）
- [ ] `xmlns="http://www.w3.org/1999/xhtml"` 与 `xmlns:epub="http://www.idpf.org/2007/ops"` 声明完整
- [ ] 缩进风格统一（4 空格，动态内容行与静态结构对齐）
- [ ] `nav.xhtml`：`<nav>` 含 `class="toc-page-nav"` + `epub:type="toc"`

### 3.3 OPF 层（`content.opf`）
- [ ] `<package>` 含 `version="3.0"` 与 `unique-identifier="bookid"`
- [ ] `<metadata>` 中 `dc:identifier` 的 `id` 与 `unique-identifier` 匹配
- [ ] `manifest` 每项 `media-type` 合法（如 `application/xhtml+xml` / `image/jpeg`）
- [ ] `spine` 的 `idref` 全部指向 manifest 存在的项
- [ ] 无重复 `id`（manifest / spine / 文件内元素）

### 3.4 容器层
- [ ] `mimetype` 文件：**ZIP 首文件、STORED 未压缩、纯文本 `application/epub+zip` 无换行**
- [ ] `META-INF/container.xml` 指向正确 OPF 路径
- [ ] 所有 manifest 声明的文件在 ZIP 中真实存在
- [ ] 无孤儿文件（ZIP 内文件均被 manifest 引用）
- [ ] **Images 目录交叉检查**：`Images/` 下所有文件必须出现在 OPF `manifest` 的 `href` 中，否则触发 Sigil「文件未被文件列表记录」警告

### 3.5 章节正文层
- [ ] 旧版生成器用 `<br /><br />` 连续换行分段（而非 `<p>` 包裹），导致 CSS 的 `p { text-indent }` 缩进失效
- [ ] 重构时检测章节 XHTML 内连续 `<br />` 分段，按双 `<br />` 切分为独立 `<p>` 段落
- [ ] `<h1>` 章节标题保持独立，不包裹进 `<p>`
- [ ] 段落内残留单 `<br />` 转为空格（符合中文排版）
- [ ] 转换后正文获得与新版一致的 `text-indent: 2em` 首行缩进阅读体验

---

## 四、实现架构

### 4.1 模块划分
```
core/
├── epub_refinery.py     # 新增：EPUB 重构核心引擎
└── epub.py             # 现有：EPUB 生成（保持不变）
tools/
└── refine_epub.py      # CLI 入口：调用 epub_refinery
```

### 4.2 `EpubRefinery` 类设计
```python
class EpubRefinery:
    """EPUB 重构引擎 —— 严格遵循 Sigil / EPUB3 规范"""

    def __init__(self, epub_path: str, backup: bool = True):
        self.epub_path = epub_path
        self.backup = backup
        self.issues = []          # 重构前诊断问题列表
        self.modifications = {}   # 文件路径 -> 新内容

    # ── 诊断阶段 ──
    def diagnose(self) -> dict:
        """全面扫描 EPUB，返回 ERROR/WARNING/INFO 分级报告"""

    # ── 修补阶段 ──
    def patch_css(self) -> None:
        """CSS 层重构：移除转义选择器、修复语法"""

    def patch_xhtml(self) -> None:
        """XHTML 层重构：统一缩进、补全命名空间、加 class"""

    def patch_opf(self) -> None:
        """OPF 层重构：校验 ID 唯一性、metadata 完整性"""

    def patch_container(self) -> None:
        """容器层重构：确保 mimetype 首文件未压缩"""

    # ── 执行阶段 ──
    def refine(self, fixes: list = None) -> dict:
        """执行重构：诊断 → 修补 → 重新打包（保留 mimetype 规范）"""

    def repackage(self) -> None:
        """ZIP 重打包：mimetype STORED 首文件 + 其余 DEFLATED"""
```

### 4.3 关键算法

#### 4.3.1 CSS 转义选择器修复
```python
import re

EPub_TYPE_PATTERNS = [
    (r'body\[epub\\?:type="titlepage"\]', 'body.title-page-body'),
    (r'nav\[epub\\?:type="toc"\]', 'nav.toc-page-nav'),
    # 扩展：其他 epub:type 用法
]

def fix_css_escapes(css: str) -> str:
    for pat, repl in EPub_TYPE_PATTERNS:
        css = re.sub(pat, repl, css)
    return css
```

#### 4.3.2 XHTML 命名空间补全
```python
def ensure_namespaces(xhtml: str) -> str:
    if 'xmlns:epub=' not in xhtml:
        xhtml = xhtml.replace(
            '<html ',
            '<html xmlns:epub="http://www.idpf.org/2007/ops" ',
            1
        )
    return xhtml
```

#### 4.3.3 mimetype 首文件保证
```python
def repackage(epub_path, modified: dict):
    with zipfile.ZipFile(epub_path, 'r') as zin:
        zin.extractall(tmp)
    # 覆盖 modified 文件
    # 重新打包：mimetype STORED 首写入，其余 DEFLATED
    with zipfile.ZipFile(epub_path, 'w') as zout:
        zout.write(mt_path, 'mimetype', ZIP_STORED)
        for f in all_files_except_mimetype:
            zout.write(f, arcname, ZIP_DEFLATED)
```

---

## 五、CLI 接口设计

### 5.1 单文件重构
```bash
python tools/refine_epub.py "novels/我成了兽人王国唯一的人类.epub"
```

### 5.2 批量重构
```bash
python tools/refine_epub.py --batch novels/
```

### 5.3 仅诊断不修改
```bash
python tools/refine_epub.py "novels/xxx.epub" --dry-run
```

### 5.4 参数说明
| 参数 | 说明 |
|------|------|
| `--backup / --no-backup` | 是否生成 `.bak` 备份（默认开） |
| `--dry-run` | 仅诊断，不写文件 |
| `--fix css,xhtml,opf,container` | 指定重构范围（默认全做） |
| `--strict` | 遇到 WARNING 也视为失败（CI 用） |

---

## 六、集成到现有流程

### 6.1 生成后自动重构
在 `core/epub.py` 的 `build_epub` 末尾调用：
```python
# 生成后立即过一遍 refinery，确保 Sigil 零警告
refinery = EpubRefinery(output_path)
refinery.refine()
```

### 6.2 main.py 新增参数
```bash
python main.py --refine-only "novels/xxx.epub"   # 仅重构不下载
python main.py -H -y URL --skip-refine            # 下载但跳过自动重构
```

---

## 七、测试用例

### 7.1 单元测试用例
1. `fix_css_escapes`：含 `epub\:type` 的 CSS → 输出无转义、含 class
2. `ensure_namespaces`：缺 `xmlns:epub` 的 XHTML → 补全
3. `repackage`：mimetype 始终为 ZIP 首文件且 STORED

### 7.2 集成测试
- 用上一轮生成的 `我成了兽人王国唯一的人类.epub` 作为基线
- 运行 `refine_epub.py` 后，用 Sigil CLI（如有）或自写校验器确认零警告
- 对比重构前后文件大小、图片完整性、章节数不变

### 7.3 回归测试
- 对 `直到我的人生走向破灭.epub`（旧版，段首缩进问题）运行重构
- 确认重构不破坏既有结构，仅优化格式层

---

## 八、风险与缓解

| 风险 | 缓解措施 |
|------|---------|
| 重打包导致图片损坏 | 仅覆盖 CSS/XHTML/OPF，图片字节原样复制；重构后校验 ZIP CRC |
| class 重复添加 | `patch_title_xhtml` 先检查 `title-page-body` 是否已存在 |
| 备份文件堆积 | `.bak` 同名覆盖；提供 `--clean-backup` 清理 |
| Sigil 版本差异 | 遵循 EPUB3 官方规范（IDPF），不依赖特定编辑器行为 |

---

## 九、交付物清单

- [x] `tools/_regen_epub.py` —— 原型修补脚本（CSS + title.xhtml 原地修复）
- [ ] `core/epub_refinery.py` —— 完整重构引擎（待实现）
- [ ] `tools/refine_epub.py` —— CLI 入口（待实现）
- [ ] `docs/EPUB重构功能设计.md` —— 本方案文档
- [ ] `tests/test_epub_refinery.py` —— 单元测试（待实现）

---

## 十、实施路线图

| 阶段 | 任务 | 状态 |
|------|------|------|
| P0 | 原型脚本 `_regen_epub.py` 验证修补逻辑 | ✅ 已完成 |
| P1 | 抽象 `EpubRefinery` 类，覆盖 CSS/XHTML/OPF/容器四层 | ⏳ 待实现 |
| P2 | CLI `refine_epub.py` + 批量/诊断模式 | ⏳ 待实现 |
| P3 | 集成到 `epub.py` 生成后自动重构 | ⏳ 待实现 |
| P4 | 单元测试 + Sigil 兼容验证 | ⏳ 待实现 |

---

> **注**：当前已交付原型脚本 `tools/_regen_epub.py`，可立即用于修复现有 EPUB 的 Sigil CSS 警告与 title.xhtml 缩进问题。完整重构引擎按路线图逐步落地。
