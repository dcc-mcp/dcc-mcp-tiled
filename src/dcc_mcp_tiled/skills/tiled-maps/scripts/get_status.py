from dcc_mcp_core.skill import run_main

from dcc_mcp_tiled.skill_tools import bridge_main

main = bridge_main("status", "Tiled CLI is ready.")

if __name__ == "__main__":
    run_main(main)
