const state = {
  files: [],
  rows: [],
  mode: "delegate",
};

const $ = (selector) => document.querySelector(selector);
const fileInput = $("#fileInput");
const dropzone = $("#dropzone");
const queueList = $("#queueList");
const resultsBody = $("#resultsBody");
const toast = $("#toast");

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("show");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove("show"), 2300);
}

function currentSource() {
  return state.mode === "platform" ? $("#sourceSelect").value : "";
}

function syncSourceState() {
  const enabled = state.mode === "platform";
  $("#sourceSelect").disabled = !enabled;
  $("#sourceMetric").textContent = enabled ? $("#sourceSelect").value.replace("来源平台 ", "平台 ") : "未指定";
}

function renderStats() {
  $("#fileCount").textContent = state.files.length;
  $("#queueCount").textContent = `${state.files.length} files`;
  $("#phoneCount").textContent = state.rows.filter((row) => row.phone !== "待复核").length;
  $("#leadCount").textContent = state.rows.length;
  $("#queueState").textContent = state.files.some((file) => file.state === "processing") ? "识别中" : state.files.length ? "已加入队列" : "尚未开始";
}

function renderQueue() {
  if (!state.files.length) {
    queueList.innerHTML = '<div class="empty-state">图片会出现在这里</div>';
    renderStats();
    return;
  }
  queueList.innerHTML = state.files.map((file, index) => `
    <div class="queue-item">
      <img class="queue-thumb" src="${file.preview}" alt="" />
      <div class="queue-meta">
        <div class="queue-name">${escapeHtml(file.name)}</div>
        <div class="queue-state ${file.state}">${file.state === "processing" ? "识别中..." : file.state === "done" ? "已完成" : "等待识别"}</div>
      </div>
      <button class="queue-remove" type="button" data-remove="${index}" title="移除图片" aria-label="移除图片">×</button>
    </div>
  `).join("");
  renderStats();
}

function renderRows() {
  const query = $("#searchInput").value.trim().toLowerCase();
  const rows = state.rows.filter((row) => [row.id, row.phone, row.source].join(" ").toLowerCase().includes(query));
  if (!rows.length) {
    resultsBody.innerHTML = '<tr class="empty-row"><td colspan="5">暂无识别结果</td></tr>';
    renderStats();
    return;
  }
  resultsBody.innerHTML = rows.map((row) => `
    <tr>
      <td>${escapeHtml(row.id)}</td>
      <td>${escapeHtml(row.phone)}</td>
      <td>${escapeHtml(row.source || "未指定")}</td>
      <td><span class="status-pill ${row.pending ? "pending" : ""}">${row.pending ? "待复核" : "已识别"}</span></td>
      <td class="align-right">${escapeHtml(row.image)}</td>
    </tr>
  `).join("");
  renderStats();
}

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[char]);
}

function addFiles(fileList) {
  const images = [...fileList].filter((file) => file.type.startsWith("image/"));
  if (!images.length) {
    showToast("请选择 PNG、JPG 或 JPEG 图片");
    return;
  }
  images.forEach((file) => {
    state.files.push({ name: file.name, preview: URL.createObjectURL(file), state: "queued", file });
  });
  renderQueue();
  showToast(`已加入 ${images.length} 张图片`);
}

function startScan() {
  if (!state.files.length) {
    showToast("请先添加截图");
    return;
  }
  const pending = state.files.filter((file) => file.state === "queued");
  if (!pending.length) {
    showToast("当前批次已经识别完成");
    return;
  }
  pending.forEach((file, index) => {
    file.state = "processing";
    window.setTimeout(() => {
      file.state = "done";
      state.rows.unshift({
        id: "待识别",
        phone: "待复核",
        source: currentSource(),
        image: file.name,
        pending: true,
      });
      renderQueue();
      renderRows();
      if (index === pending.length - 1) {
        $("#statusText").textContent = "演示识别完成 · 等待 OCR 接口接入";
        showToast("演示识别完成，请复核结果");
      }
    }, 650 + index * 420);
  });
  $("#statusText").textContent = "演示识别进行中...";
  renderQueue();
}

async function pasteImage() {
  if (!navigator.clipboard?.read) {
    showToast("当前浏览器不允许读取图片剪贴板，请使用添加图片");
    return;
  }
  try {
    const items = await navigator.clipboard.read();
    const imageBlobs = [];
    for (const item of items) {
      const type = item.types.find((itemType) => itemType.startsWith("image/"));
      if (type) imageBlobs.push(await item.getType(type));
    }
    if (!imageBlobs.length) {
      showToast("剪贴板里没有图片");
      return;
    }
    addFiles(imageBlobs.map((blob, index) => new File([blob], `paste-${Date.now()}-${index + 1}.png`, { type: blob.type })));
  } catch {
    showToast("读取剪贴板失败，请检查浏览器权限");
  }
}

async function copyBatch() {
  if (!state.rows.length) {
    showToast("当前批次没有可复制的记录");
    return;
  }
  const text = state.rows.map((row) => [row.id, row.phone, row.source || "", row.image].join("\t")).join("\n");
  try {
    await navigator.clipboard.writeText(text);
    showToast("当前批次已复制");
  } catch {
    showToast("复制失败，请检查浏览器权限");
  }
}

$("#fileButton").addEventListener("click", () => fileInput.click());
$("#dropzone").addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", (event) => addFiles(event.target.files));
$("#scanButton").addEventListener("click", startScan);
$("#copyButton").addEventListener("click", copyBatch);
$("#themeToggle").addEventListener("click", () => {
  const root = document.documentElement;
  const next = root.dataset.theme === "light" ? "dark" : "light";
  root.dataset.theme = next;
  localStorage.setItem("lead-scanner-theme", next);
});
$("#searchInput").addEventListener("input", renderRows);
$("#sourceSelect").addEventListener("change", syncSourceState);

document.querySelectorAll("[data-mode]").forEach((button) => {
  button.addEventListener("click", () => {
    state.mode = button.dataset.mode;
    document.querySelectorAll("[data-mode]").forEach((item) => item.classList.toggle("active", item === button));
    syncSourceState();
  });
});

dropzone.addEventListener("dragover", (event) => { event.preventDefault(); dropzone.classList.add("dragover"); });
dropzone.addEventListener("dragleave", () => dropzone.classList.remove("dragover"));
dropzone.addEventListener("drop", (event) => { event.preventDefault(); dropzone.classList.remove("dragover"); addFiles(event.dataTransfer.files); });

document.addEventListener("paste", (event) => {
  const image = [...(event.clipboardData?.items || [])].find((item) => item.type.startsWith("image/"));
  if (!image) return;
  addFiles([new File([image.getAsFile()], `paste-${Date.now()}.png`, { type: image.type })]);
});

queueList.addEventListener("click", (event) => {
  const button = event.target.closest("[data-remove]");
  if (!button) return;
  const index = Number(button.dataset.remove);
  URL.revokeObjectURL(state.files[index].preview);
  state.files.splice(index, 1);
  renderQueue();
});

document.documentElement.dataset.theme = localStorage.getItem("lead-scanner-theme") || "dark";
syncSourceState();
renderQueue();
renderRows();
