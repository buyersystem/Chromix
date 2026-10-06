(() => {
  "use strict";

  const languageButton = document.querySelector("#language-toggle");
  const languageLabel = document.querySelector("#language-label");
  const menuButton = document.querySelector("#menu-toggle");
  const navigation = document.querySelector("#navigation");
  const status = document.querySelector("#copy-status");
  const translations = [...document.querySelectorAll("[data-en]")].map((element) => ({
    element,
    chinese: [...element.childNodes].map((node) => node.cloneNode(true)),
    english: element.dataset.en,
  }));
  const labels = [...document.querySelectorAll("[data-en-label]")].map((element) => ({
    element,
    chinese: element.getAttribute("aria-label"),
    english: element.dataset.enLabel,
  }));
  let language = "zh";
  let statusTimer;

  function updateMenuLabel() {
    const isOpen = menuButton.getAttribute("aria-expanded") === "true";
    menuButton.setAttribute("aria-label", language === "en"
      ? (isOpen ? "Close navigation" : "Open navigation")
      : (isOpen ? "关闭导航" : "打开导航"));
  }

  function setLanguage(nextLanguage) {
    language = nextLanguage === "en" ? "en" : "zh";
    document.documentElement.lang = language === "en" ? "en" : "zh-CN";
    translations.forEach(({ element, chinese, english }) => {
      if (language === "en") {
        element.textContent = english;
      } else {
        element.replaceChildren(...chinese.map((node) => node.cloneNode(true)));
      }
    });
    labels.forEach(({ element, chinese, english }) => {
      element.setAttribute("aria-label", language === "en" ? english : chinese);
    });
    languageLabel.textContent = language === "en" ? "中文" : "EN";
    languageButton.setAttribute("aria-label", language === "en" ? "切换到中文" : "Switch to English");
    document.title = language === "en" ? "Chromix — Your browser. Your definition." : "Chromix — 浏览器，由你定义。";
    document.querySelector('meta[name="description"]').content = language === "en"
      ? "Chromix: a configurable Chromium browser with Python and Node.js SDKs. Built for reproducible browser automation, compatibility testing, and research."
      : "Chromix：可配置的 Chromium 浏览器与 Python、Node.js SDK。为可复现的浏览器自动化、兼容性测试与研究而构建。";
    updateMenuLabel();
    clearTimeout(statusTimer);
    status.classList.remove("is-visible");
    status.textContent = "";
    try {
      localStorage.setItem("chromix-language", language);
    } catch {
      // Language switching also works when browser storage is unavailable.
    }
  }

  languageButton.addEventListener("click", () => setLanguage(language === "zh" ? "en" : "zh"));
  try {
    if (localStorage.getItem("chromix-language") === "en") setLanguage("en");
  } catch {
    // Chinese remains the default when browser storage is unavailable.
  }

  function setMenu(isOpen, restoreFocus = false) {
    navigation.classList.toggle("is-open", isOpen);
    menuButton.setAttribute("aria-expanded", String(isOpen));
    updateMenuLabel();
    if (restoreFocus) menuButton.focus();
  }

  menuButton.addEventListener("click", () => {
    setMenu(menuButton.getAttribute("aria-expanded") !== "true");
  });
  navigation.addEventListener("click", (event) => {
    if (event.target.closest("a")) setMenu(false);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && menuButton.getAttribute("aria-expanded") === "true") {
      setMenu(false, true);
    }
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".site-header")) setMenu(false);
  });
  const mobileQuery = window.matchMedia("(max-width: 800px)");
  mobileQuery.addEventListener("change", () => setMenu(false));

  const tabs = [...document.querySelectorAll('[role="tab"]')];
  function activateTab(activeTab, focus = false) {
    tabs.forEach((tab) => {
      const isSelected = tab === activeTab;
      tab.setAttribute("aria-selected", String(isSelected));
      tab.tabIndex = isSelected ? 0 : -1;
      document.getElementById(tab.getAttribute("aria-controls")).hidden = !isSelected;
    });
    if (focus) activeTab.focus();
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => activateTab(tab));
    tab.addEventListener("keydown", (event) => {
      let nextIndex;
      if (event.key === "ArrowRight") nextIndex = (index + 1) % tabs.length;
      if (event.key === "ArrowLeft") nextIndex = (index - 1 + tabs.length) % tabs.length;
      if (event.key === "Home") nextIndex = 0;
      if (event.key === "End") nextIndex = tabs.length - 1;
      if (nextIndex !== undefined) {
        event.preventDefault();
        activateTab(tabs[nextIndex], true);
      }
    });
  });

  function fallbackCopy(text) {
    const previousFocus = document.activeElement;
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.className = "copy-fallback";
    textarea.setAttribute("aria-label", language === "en" ? "Copy text" : "复制文本");
    textarea.readOnly = true;
    document.body.append(textarea);
    textarea.select();
    textarea.setSelectionRange(0, text.length);
    let copied = false;
    try {
      copied = document.execCommand("copy");
    } finally {
      textarea.remove();
      if (previousFocus instanceof HTMLElement) previousFocus.focus({ preventScroll: true });
    }
    return copied;
  }

  function announce(message) {
    clearTimeout(statusTimer);
    status.textContent = message;
    status.classList.add("is-visible");
    statusTimer = setTimeout(() => {
      status.classList.remove("is-visible");
      status.textContent = "";
    }, 4500);
  }

  document.querySelectorAll("[data-copy]").forEach((button) => {
    button.addEventListener("click", async () => {
      const text = document.getElementById(button.dataset.copy).textContent.trim();
      let copied = false;
      try {
        if (navigator.clipboard && window.isSecureContext) {
          await navigator.clipboard.writeText(text);
          copied = true;
        }
      } catch {
        // A denied clipboard permission may still allow a selection-based copy.
      }
      if (!copied) {
        try {
          copied = fallbackCopy(text);
        } catch {
          copied = false;
        }
      }
      announce(copied
        ? (language === "en" ? "Copied to clipboard." : "已复制到剪贴板。")
        : (language === "en" ? "Copy unavailable. Please select and copy the command manually." : "无法自动复制，请选中文本后手动复制。"));
    });
  });
})();
