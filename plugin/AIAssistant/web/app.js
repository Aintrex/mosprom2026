"use strict";
const form = document.querySelector("#chat-form");
const input = document.querySelector("#message");
const send = document.querySelector("#send");
const status = document.querySelector("#status");
const messages = document.querySelector("#messages");

function appendMessage(role, text) {
  const article = document.createElement("article");
  const label = document.createElement("strong");
  const body = document.createElement("p");
  article.className = role;
  label.textContent = role === "user" ? "Вы" : "Ассистент";
  body.textContent = text;
  article.append(label, body);
  messages.append(article);
  article.scrollIntoView({block: "nearest"});
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text || send.disabled) return;
  appendMessage("user", text);
  send.disabled = true;
  status.textContent = "Ожидание ответа…";
  try {
    const response = await fetch("/api/message", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({text})
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "Ошибка сервера");
    appendMessage("assistant", data.reply);
    input.value = "";
    status.textContent = "Демонстрационный ответ получен";
  } catch (error) {
    status.textContent = "Не удалось отправить запрос: " + error.message;
  } finally {
    send.disabled = false;
    input.focus();
  }
});

fetch("/api/health").then((response) => {
  if (!response.ok) throw new Error("HTTP " + response.status);
  status.textContent = "Локальный сервер подключён · режим каркаса";
}).catch(() => { status.textContent = "Локальный сервер недоступен"; });
