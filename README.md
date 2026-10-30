# Codex Mihomo

在一台 Linux 服务器上跑回环 Mihomo，让 Codex CLI 走健康节点访问 OpenAI。节点选择、订阅切换和 Codex 健康检查都在服务器本机完成。这不是整机透明代理：只有设置了 `http://127.0.0.1:19082` 的程序会经过它。

订阅 URL、节点凭据、运行日志和机器路径不在这个仓库里。每台机器用自己的设置文件和运行目录。

## 两个进程，一个入口

| 进程 | 作用 |
| --- | --- |
| Mihomo | 混合代理，默认 `127.0.0.1:19082`；控制口默认 `127.0.0.1:19083` |
| 稳定选点 | 每 10 秒读取 Codex 健康检查历史，切换 `OUTBOUND` |

前台一条命令同时拉起两个进程：

```sh
python3 -m codex_mihomo up
```

交给 supervisord 或 systemd 时，两个程序都是 `autostart=true`、`autorestart=true`。`install` 会一次写好两边的配置。

## 安装

需要 Python 3.10+ 和 PyYAML。Mihomo 从 [MetaCubeX releases](https://github.com/MetaCubeX/mihomo/releases) 下载，放到设置里的 `mihomo_bin`，并加上可执行权限。

```sh
python3 -m pip install -r requirements.txt
python3 -m codex_mihomo init
```

`init` 把 `settings.example.yaml` 复制到 `~/.config/codex-mihomo/settings.yaml`（权限 600）。默认运行目录是 `~/.codex-mihomo`，和任何已经在用的私有运行目录分开。要接管一个已有实例，再把 `home` 指到那个目录。

常用项：

| 设置 | 默认 | 作用 |
| --- | --- | --- |
| `mixed_port` / `controller_port` | 19082 / 19083 | 只监听 `bind_address`，且必须是回环地址 |
| `health_url` / `expected_status` | Codex responses / 405 | 节点健康检查。405 只说明接口可达 |
| `exclude_filter` | 排除香港和订阅提示行 | 可改成这台机器自己的规则 |
| `include_filter` | 空 | 空表示不按地区白名单，只使用排除规则 |
| `codex_bin` / `codex_home` | `codex` / 空 | 空的 `codex_home` 不覆盖 Codex 自己的目录 |
| `supervisor_config` | 空 | 这台机器的 supervisord 配置路径，留空则不注册 |

环境变量 `CODEX_MIHOMO_CONFIG`、`CODEX_MIHOMO_HOME`、`CODEX_MIHOMO_BIN` 可以覆盖设置文件。

## 接入订阅

名称由你指定，仓库不内置服务商名单。URL 从隐藏输入或私有文件读取，不会进入命令行参数。

```sh
python3 -m codex_mihomo provider register alpha
python3 -m codex_mihomo provider list
python3 -m codex_mihomo provider check alpha
python3 -m codex_mihomo provider use alpha
```

`use` 会校验配置、热加载，并探测健康检查。探测失败会恢复上一份配置。`refresh alpha` 更新缓存；`refresh all` / `check all` 作用于已经登记的全部名称。

订阅下载先走当前代理，失败再直连。下载路由可以在登记时指定：

```sh
python3 -m codex_mihomo provider register alpha --download-route DIRECT --url-file ~/private-url.txt
```

已有 Clash/Mihomo YAML、但服务器还下不到订阅时：

```sh
python3 -m codex_mihomo provider register alpha --profile-file ~/private-provider.yaml --url-file ~/private-url.txt
```

这两个文件只放在服务器私有目录，不要放进仓库。

## 启动

前台：

```sh
python3 -m codex_mihomo up
```

Mihomo 异常退出时，这条命令会停掉选点进程并返回非零，方便 systemd 把两个进程一起拉起来。

注册到这台机器已有的 supervisord，一次加入两个程序：

```sh
python3 -m codex_mihomo install --supervisor-config /path/to/supervisord.conf
supervisorctl -c /path/to/supervisord.conf status codex-mihomo codex-mihomo-health
```

用户级 systemd：

```sh
python3 -m codex_mihomo install --systemd user --enable
```

`--enable` 才会执行 `systemctl enable --now`。没有这台机器的写权限时，单元文件留在运行目录里。

交互式 shell 需要自己选择是否写入启动脚本：

```sh
python3 -m codex_mihomo install --shell-rc ~/.bashrc
```

然后重新打开 shell，或执行 `. ~/.codex-mihomo/codex-env.sh`。Codex 包装命令在 `~/.codex-mihomo/bin/codex`，它设置代理后调用真正的 `codex`，避免包装脚本调用自己。

## 日常切换

编号在订阅更新后会变，切换前先看一遍。

```sh
python3 -m codex_mihomo control status
python3 -m codex_mihomo control nodes
python3 -m codex_mihomo control pin S2
python3 -m codex_mihomo control auto
python3 -m codex_mihomo control refresh
python3 -m codex_mihomo doctor
```

`B1` 是引导节点，`S1` 是在线订阅节点。`pin` 在节点健康时保持手动选择，节点失败时仍会让开。`auto` 删掉手动固定。

## 选点规则

- `OUTBOUND` 是 `select`，只引用当前订阅。稳定选点进程按 Codex 专用历史打分，不把普通联网检测混进去。
- 保留最近 20 个不同时间戳的样本。分数是失败比例 × 10000 + 成功延迟中位数 + 0.5 ×（成功延迟 P90 − 中位数）。
- 最近一次失败后冷却 180 秒，并且要连续成功 3 次才重新成为候选。超过 40 秒没有新样本的节点不参与。
- 正常优化至少保持当前出口 120 秒；分数改善至少 150 且至少 20% 才切换。当前出口不健康时立刻换合格备用。
- 整个池都在冷却时，允许已经连续成功 3 次的节点紧急恢复。没有任何成功证据时保持当前选择，不改成直连。
- 引导组 `BOOTSTRAP` 仍是 `url-test`，供订阅下载使用，默认惰性检测。

10 秒是检测周期，不是断线之后的恢复时限。已经断开的 Codex 流需要自己重连。

## 换一台机器

复制这个目录，或者 `pip install` 之后在新机器执行 `init`。每台机器单独放置 Mihomo 可执行文件、设置文件和订阅。端口、supervisord 配置路径和 Codex 安装位置都写在那台机器的设置里。

平台如果重建并覆盖了 supervisord 主配置，重新执行带 `--supervisor-config` 的 `install`。已有的 `files =` 行会追加这个运行目录的 ini，不会另起一个 `[include]` 段。

## 故障判断

1. `doctor` 显示端口 down：看运行目录里的 `service.log`，确认 `up` 或 supervisord 里的两个程序都在。
2. 端口在听，但健康检查不是预期状态：`control nodes`、`control auto`、`control refresh`。
3. 代理可达而 Codex 仍失败：新开的终端里 `HTTPS_PROXY` 应指向混合端口。已经启动的 Codex 进程不会自动继承后改的环境。
4. 其他程序上不了网：先看它有没有设置代理。这个服务不会接管整台机器的流量。

## 测试

```sh
python3 -m unittest discover -s tests -v
```

测试使用 `example.invalid` 和内存里的假节点，不连接真实订阅，也不读取本机已有的私有运行目录。
