# ai-mcp-server
### DeepSeek++ 的MCP使用

> - 其他复杂的或需要key的json可能会报错，有能力者可以完善代码
> - 首次启动会安装配置文件或进程，请等待
> - npx命令默认安装程序到当前目录，拒绝占用C盘空间


### 默认添加了
- 12306
- excel
- Markdown

### DeepSeek++ MCP 
```
传输  Streamable HTTP
服务 URL  http://localhost:9999/mcp
结果字节  1000000
```


### 添加服务器json
```
{
  "mcpServers": {
    "12306-mcp": {
      "command": "npx",
      "args": [
        "-y",
        "12306-mcp"
      ]
    }
  }
}
```

