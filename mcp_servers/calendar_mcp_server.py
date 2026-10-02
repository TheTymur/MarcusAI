from fastmcp import FastMCP

mcp = FastMCP("calendar-server")





if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
