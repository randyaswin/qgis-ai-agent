def classFactory(iface):
    from .plugin import AIAgentPlugin
    return AIAgentPlugin(iface)
