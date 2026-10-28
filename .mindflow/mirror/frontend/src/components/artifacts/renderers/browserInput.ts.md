---
code_file: frontend/src/components/artifacts/renderers/browserInput.ts
last_verified: 2026-09-23
stub: false
---

# 浏览器输入坐标

画布用 object-contain 显示远程 viewport。鼠标坐标必须先扣除上下或左右留白，
再从图像像素换算到帧 metadata 的 deviceWidth/deviceHeight 页面坐标；JPEG 会独立缩放，
不能假设其像素尺寸就是页面 viewport。留白点击不发送，零尺寸布局不除零。CDP 修饰键位图集中转换，
让键盘、鼠标和滚轮保持同一种协议。纯函数测试覆盖两种留白及边缘坐标。
