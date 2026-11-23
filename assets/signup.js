// Progressive enhancement: "Sign up" copies the /claim command, then the link
// opens the issue so the person can paste it as a comment. Without JS the link
// still opens the issue and the command is shown next to it.
document.addEventListener("click", async (event) => {
  const link = event.target.closest("a.claim[data-cmd]");
  if (!link || !navigator.clipboard) return;
  try {
    await navigator.clipboard.writeText(link.dataset.cmd + " ");
    const status = document.createElement("span");
    status.className = "copied";
    status.setAttribute("role", "status");
    status.textContent = "Copied. Paste it as a comment on the issue, add a note if you like.";
    link.parentElement.querySelector(".copied")?.remove();
    link.parentElement.append(status);
  } catch {
    // Clipboard blocked: the visible <code> command is the fallback.
  }
});
