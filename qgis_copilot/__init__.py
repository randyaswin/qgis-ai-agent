def classFactory(iface):
    import sys
    # Purge cached submodules so newly updated files reload cleanly
    for mod in list(sys.modules.keys()):
        if mod.startswith("qgis_copilot."):
            del sys.modules[mod]

    from .plugin import AIAgentPlugin
    return AIAgentPlugin(iface)
