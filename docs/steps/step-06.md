# Step 6：Skill 系统（渐进式披露）

结构（agentskills / OpenClaw 同款简化版）：

```text
skills/
  hello-neko/
    SKILL.md
  web-search/
    SKILL.md
    reference.md          # LoadSkill 时一并返回
src/bc_code_agent/
  skill_loader.py         # 扫描 / 解析 / catalog / view
  start.py                # LoadSkill + WebSearch + Read/Write/Grep/Glob/Shell + Task
```

**渐进式披露**：

1. 启动时扫描 `skills/*/SKILL.md`，system 里只放 `name` + `description` 目录  
2. 任务匹配后调用 `LoadSkill(name)` → 返回正文 + 同目录文本资源  
3. 需要上网时再按 Skill 说明调用 `WebSearch`（`ddgs`，无需 API Key）  

不要一把把所有 Skill body 塞进 system。

### 实录（2026-08-09）：查北京天气

```text
[Skill] indexed: hello-neko (.../skills/hello-neko/SKILL.md)
[Skill] indexed: web-search (.../skills/web-search/SKILL.md)

Enter a prompt: 帮我查询一下今天北京的天气情况

[Tool]: LoadSkill({'name': 'web-search'})
[Tool Result]: # Skill: `web-search`
Description: 当需要查实时信息、新闻、资料、事实核对……时使用；指导如何调用 WebSearch 并整理结果。
...
## Instructions
1. 先判断要不要搜
2. 调用工具 WebSearch（query / max_results）
3. 整理回答（禁止编造）
...
## Resource: `reference.md`
# WebSearch 查询小技巧
...

[Tool]: WebSearch({'query': '北京今日天气', 'max_results': 5})
[Tool Result]: 1. 北京今日天气晴好 风力不大
   URL: https://www.weather.com.cn/beijing/sygdt/02/4510086.shtml
   北京今日天气晴好，风力不大，气温较昨天也略有上升。

2. 北京今日天气|天气API|tianqiapi.com
   URL: https://widget.tianqiapi.com/?style=yf&skin=pitaya
   北京天气. 多云转晴.

3. {%$ROOT.TITLE%}
   URL: https://m.hao123.com/a/tianqi/
   北京今日天气. 2026.06.25 星期四. 今日 晴 31/20°C ... 空气质量 ... 现在26. 优 ...

4. 【北京天气】北京天气预报...
   URL: http://tianqi.95447.com/beijing/beijing/
   ...

[Agent]: 小猫帮主人查到北京天气啦，整理如下喵～

🌤️ 北京今日天气情况：
- 天气状况：晴好，局部多云转晴，风力不大
- 气温：今日 31°C / 20°C
- 明日预报：多云，32°C / 21°C
- 空气质量：当前约 26，属于「优」

来源参考：
- 中国天气网 - 北京天气
- hao123 北京天气

主人还需要了解其他城市的天气吗？喵～
```

链路：`目录匹配 → LoadSkill → WebSearch → 总结回答`。新增 Skill：在 `skills/<name>/` 下加 `SKILL.md`，重启即可。

