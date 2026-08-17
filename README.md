# ai-mcp-server
## 一个把agent MCP服务转成Streamable HTTP格式的项目
### DeepSeek++ 的MCP使用

> - 其他复杂的或需要key的json可能会报错，有能力者可以完善代码
> - 首次启动会安装配置文件或进程，请等待
> - npx命令默认安装程序到当前目录，拒绝占用C盘空间

![image](https://jasuimg.2091k.cn/2091k/image/main/001/20260818073354_1gqhiv6o0u.png)


### 默认添加了
- 12306
- excel
- ssh

### DeepSeek++ MCP 
```
传输  Streamable HTTP
服务 URL  http://localhost:9999/mcp
结果字节  1000000
```


### 添加服务器json

- 12306
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
- excel文件操作

```
{
  "mcpServers": {
    "excel": {
      "args": [
        "-y",
        "@zhiweixu/excel-mcp-server"
      ],
      "command": "npx",
      "env": {
        "CACHE_CLEANUP_INTERVAL": "4",
        "CACHE_MAX_AGE": "1",
        "LOG_CLEANUP_INTERVAL": "24",
        "LOG_PATH": "[set an accessible absolute path]",
        "LOG_RETENTION_DAYS": "7"
      },
      "type": "stdio"
    }
  }
}
```

- ssh 连接到你的SSH

```
{
  "mcpServers": {
    "ssh-mcp-server": {
      "command": "npx",
      "args": [
        "-y",
        "@fangjunjie/ssh-mcp-server",
        "--host",
        "192.168.1.20",
        "--port",
        "22",
        "--username",
        "root",
        "--password",
        "你的密码"
      ]
    }
  }
}
```
- amap 高德地图mcp

```
{
  "mcpServers": {
    "amap-maps": {
      "command": "npx",
      "args": [
        "-y",
        "@amap/amap-maps-mcp-server"
      ],
      "env": {
        "AMAP_MAPS_API_KEY": "你的key"
      }
    }
  }
}
```

- Chrome-MCP-Server-Portable 浏览器， 安装好后json改成路径格式

1.
```
{
  "mcpServers": {
    "12306-mcp": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-chrome-bridge"
      ]
    }
  }
}
```
2.
```
{
  "mcpServers": {
    "chrome-mcp-stdio": {
      "command": "node",
      "args": [
        "D:\\bt\\x86\\deepseek++\\mcp\\chromemcp\\node_modules\\mcp-chrome-bridge\\dist\\mcp\\mcp-server-stdio.js"
      ]
    }
  }
}
```
