return {
  "nvim-neo-tree/neo-tree.nvim",
  opts = {
    window = {
      mappings = {
        ["<S-o>"] = {
          function(state)
            local node = state.tree:get_node()
            if not node or node.type == "directory" then
              vim.notify("请选择一个文件后再预览", vim.log.levels.INFO)
              return
            end
            local path = node:get_id()
            vim.fn.jobstart({ "rdo", path }, { detach = true })
          end,
          desc = "Preview through remote-dev",
        },
      },
    },
  },
}
