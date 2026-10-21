/**
 * Electron 主进程：启动后端、等它就绪、加载界面。
 *
 * 三个必须处理好的点：
 *   1. **后端进程的生命周期**：窗口关掉要连带杀掉 uvicorn，否则下次启动端口会残留。
 *   2. **启动失败的可见性**：后端挂了不能只留一个白窗口，要弹对话框说明原因。
 *   3. **不固定端口**：后端自己挑空闲端口，用 stdout 的 `READY <url>` 回报。
 */

const { spawn } = require("node:child_process");
const os = require("node:os");
const path = require("node:path");
const fs = require("node:fs");

// 冒烟模式：加载完成后把结果写文件并退出，方便无人值守验证打包产物。
// 用环境变量而不是命令行开关 —— 打包后的 GUI 程序会把未知的 --xxx 当错误参数拒绝。
const SMOKE = process.env.GAOKAO_SMOKE === "1";
const SMOKE_FILE = path.join(os.tmpdir(), "gaokao-smoke.txt");
function smokeLog(line) {
  if (!SMOKE) return;
  try {
    fs.appendFileSync(SMOKE_FILE, `${line}\n`, "utf8");
  } catch {
    /* 日志失败不影响启动 */
  }
}

// 先落一行再 require electron：万一主进程拿不到 electron API，
// 也能在日志里看到，而不是静默崩溃。
smokeLog(`PRELOAD node=${process.versions.node} electron=${process.versions.electron ?? "-"}`);

const electron = require("electron");
const { app, BrowserWindow, dialog, shell } = electron;

smokeLog(`ELECTRON app=${typeof app} packaged=${app && app.isPackaged}`);

const IS_DEV = !app.isPackaged;

/** 后端可执行文件位置：打包后在 resources/backend，开发时用 PyInstaller 产物。 */
function backendExe() {
  if (IS_DEV) {
    return path.join(__dirname, "..", "backend", "dist", "gaokao", "gaokao.exe");
  }
  return path.join(process.resourcesPath, "backend", "gaokao.exe");
}

let backend = null;
let mainWindow = null;
let shuttingDown = false;

/** 启动后端，返回它汇报的 URL。 */
function startBackend() {
  return new Promise((resolve, reject) => {
    const exe = backendExe();
    if (!fs.existsSync(exe)) {
      reject(new Error(`找不到后端程序：\n${exe}`));
      return;
    }

    backend = spawn(exe, ["--no-open"], {
      cwd: path.dirname(exe),
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });

    let buffer = "";
    let settled = false;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      reject(new Error("后端启动超时（60 秒）"));
    }, 60_000);

    backend.stdout.setEncoding("utf8");
    backend.stdout.on("data", (chunk) => {
      buffer += chunk;
      const match = buffer.match(/READY (http:\/\/\S+)/);
      if (match && !settled) {
        settled = true;
        clearTimeout(timer);
        resolve(match[1]);
      }
    });

    backend.stderr.setEncoding("utf8");
    backend.stderr.on("data", (chunk) => {
      // uvicorn 把日志写在 stderr，留着便于排查
      console.log(`[backend] ${chunk}`.trimEnd());
    });

    backend.on("error", (error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      reject(error);
    });

    backend.on("exit", (code) => {
      if (settled || shuttingDown) return;
      settled = true;
      clearTimeout(timer);
      reject(new Error(`后端进程提前退出（exit ${code}）`));
    });
  });
}

function createWindow(url) {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 960,
    minWidth: 1180,
    minHeight: 760,
    title: "山东高考智能志愿",
    backgroundColor: "#f4f6fa",
    autoHideMenuBar: true,
    show: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  mainWindow.once("ready-to-show", () => mainWindow.show());
  mainWindow.loadURL(url);

  if (SMOKE) {
    mainWindow.webContents.once("did-finish-load", async () => {
      let line;
      try {
        const title = await mainWindow.webContents.executeJavaScript("document.title");
        const hasShell = await mainWindow.webContents.executeJavaScript(
          "!!document.querySelector('.page-head')",
        );
        line = `SMOKE_OK url=${url} title=${title} shell=${hasShell}`;
      } catch (error) {
        line = `SMOKE_FAIL ${error.message}`;
      }
      smokeLog(line);
      console.log(line);
      app.quit();
    });
  }

  // 外链走系统浏览器，不在应用内开新窗口
  mainWindow.webContents.setWindowOpenHandler(({ url: target }) => {
    if (!target.startsWith(url)) shell.openExternal(target);
    return { action: "deny" };
  });

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

function stopBackend() {
  if (!backend || backend.killed) return;
  shuttingDown = true;
  try {
    // Windows 上 taskkill /T 才能连带子进程
    if (process.platform === "win32") {
      spawn("taskkill", ["/pid", String(backend.pid), "/T", "/F"], { windowsHide: true });
    } else {
      backend.kill("SIGTERM");
    }
  } catch {
    backend.kill();
  }
  backend = null;
}

if (SMOKE) {
  try {
    fs.writeFileSync(SMOKE_FILE, "", "utf8");
  } catch {
    /* ignore */
  }
}
smokeLog(`PRELOAD packaged=${app.isPackaged}`);

// 单实例：重复双击时聚焦已有窗口，而不是再起一个后端
if (!app.requestSingleInstanceLock()) {
  smokeLog("NO_LOCK 已有实例在运行");
  app.quit();
} else {
  app.on("second-instance", () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  smokeLog(`BOOT packaged=${app.isPackaged} resources=${process.resourcesPath}`);
  app.whenReady().then(async () => {
    try {
      smokeLog(`EXE ${backendExe()} exists=${fs.existsSync(backendExe())}`);
      const url = await startBackend();
      smokeLog(`BACKEND ${url}`);
      createWindow(url);
    } catch (error) {
      smokeLog(`START_FAIL ${error.message}`);
      dialog.showErrorBox(
        "启动失败",
        `${error.message}\n\n可以尝试重新安装，或以管理员身份运行。`,
      );
      app.quit();
    }
  });

  app.on("window-all-closed", () => app.quit());
  app.on("before-quit", stopBackend);
  app.on("will-quit", stopBackend);
  process.on("exit", stopBackend);
}
