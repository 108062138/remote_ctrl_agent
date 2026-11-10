"""Remote Safety Policy：判断 Claude 的一次工具调用是 Allow、Ask 还是 Deny。

  Allow  自动放行：只有「确定只读」才放行（白名单：指令 + 每个选项都认得）
  Ask    手机上跳出允许 / 拒绝：可逆的修改、执行程式、看不懂的选项
  Deny   直接拒绝，手机批准也没用：不可逆、范围大、碰到机密、关掉安全层

原则：不确定时往严格的方向走。认不得的只读选项 → Ask；认不得的包装或语法 → Deny。

阅读顺序（每个档案只做一件事）：
  core.py       Decision（判断结果）、Context（工作目录、家目录、受保护路径）
  paths.py      路径分类：机密 / 受保护 / 工作目录内 / 暂存 / 外面 / 不确定
                （会解开 symlink、展开 {a,b} 和万用字元，且有数量上限避免卡住）
  options.py    解析指令选项（-la、-n 5、--max-depth=3、-oFILE）
  bash.py       Bash 指令：用 bashlex 拆开组合指令，去掉 env/timeout 等包装，追踪 cd
  commands.py   各指令的规则：只读白名单、git、rm、curl、sed、find……
  tools.py      非 Bash 工具（Read、Write、Edit、Grep、Glob……）和总入口 decide()
  levels.py     安全等级：readonly 把「询问」变成「拒绝」，Bash 只放行单纯指令

限制：执行脚本（python x.py、npm test、make）时，脚本内容无法分析，一律 Ask，
请在手机上看清楚再批准。这层是「远端模式的护栏」，不是沙箱。
"""

from .core import ALLOW, ASK, DENY, Context, Decision, make_context, worst
from .paths import classify_path, resolve_word
from .bash import analyze_bash, judge_argv
from .tools import decide
from .levels import LEVELS, NORMAL, READONLY, apply_level, parse_level

__all__ = [
    "ALLOW", "ASK", "DENY", "Context", "Decision", "make_context", "worst",
    "classify_path", "resolve_word", "analyze_bash", "judge_argv", "decide",
    "LEVELS", "NORMAL", "READONLY", "apply_level", "parse_level",
]
