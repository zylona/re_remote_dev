local function preview_remote_file(state)
  local node = state.tree:get_node()
  if not node or node.type == "directory" then
    vim.notify("请选择一个文件后再预览", vim.log.levels.INFO)
    return
  end
  local path = node:get_id()
  if vim.fn.executable("rdo") ~= 1 then
    vim.notify("当前会话没有 rdo；请使用 tssh 重新连接，或重新恢复远程环境", vim.log.levels.ERROR)
    return
  end
  local last_notice = 0
  local last_path = nil
  local notice_id = nil
  local notify_provider = nil
  pcall(function()
    notify_provider = require("notify")
  end)
  local function update_notice(message, level)
    if notify_provider then
      notice_id = notify_provider(message, level, {
        title = "rdo",
        replace = notice_id,
      })
    else
      -- Native vim.notify does not guarantee replacement semantics.  Use the
      -- command line as a single in-place fallback instead of stacking popups.
      vim.api.nvim_echo({ { message, level == vim.log.levels.ERROR and "ErrorMsg" or "MoreMsg" } }, false, {})
    end
  end
  local function notify_progress(lines)
    local text = table.concat(lines or {}, "\n")
    text = text:gsub("\27%[[0-9;]*[A-Za-z]", ""):gsub("\r", "")
    for line in text:gmatch("[^\n]+") do
      line = vim.trim(line)
      if line ~= "" and line:find("rdo:", 1, true) then
        local now = vim.uv.now()
        if line:find("存储路径：", 1, true) then
          last_path = line:match("存储路径：(.+)$")
        end
        -- Progress updates can arrive many times per second. Keep the Nvim
        -- message area responsive while still showing large-file progress.
        if now - last_notice >= 500 then
          last_notice = now
          update_notice(line, vim.log.levels.INFO)
        end
      end
    end
  end
  local job_id = vim.fn.jobstart({ "rdo", path }, {
    detach = false,
    env = { RDO_PROGRESS_FORMAT = "lines" },
    on_stdout = function(_, data)
      notify_progress(data)
    end,
    on_stderr = function(_, data)
      notify_progress(data)
    end,
    on_exit = function(_, code)
      if code == 0 then
        local suffix = last_path and ("：" .. last_path) or ""
        update_notice("下载完成，已打开本地查看器" .. suffix, vim.log.levels.INFO)
      else
        update_notice("下载失败，请查看终端中的 rdo 输出", vim.log.levels.ERROR)
      end
    end,
  })
  if job_id <= 0 then
    vim.notify("无法启动 rdo 预览命令", vim.log.levels.ERROR)
  end
end

return {
  "nvim-neo-tree/neo-tree.nvim",
  opts = {
    window = {
      mappings = {
        -- Neo-tree receives Shift+O from terminals as the literal `O`.
        -- Keep the angle-bracket spelling as a compatibility alias for GUI
        -- clients that preserve the modifier in the key notation.
        ["O"] = { preview_remote_file, desc = "Preview through remote-dev" },
        ["<S-o>"] = { preview_remote_file, desc = "Preview through remote-dev" },
      },
    },
  },
}
