export class ToolRegistry {
  constructor(tools = []) {
    this.tools = new Map(tools.map((tool) => [tool.name, tool]));
  }

  route(text) {
    for (const tool of this.tools.values()) {
      const route = tool.route?.(text);
      if (route) return { ...route, tool: tool.name };
    }
    return null;
  }

  async run(name, context) {
    const tool = this.tools.get(name);
    if (!tool) throw new Error(`未知工具: ${name}`);
    return tool.execute(context);
  }
}
