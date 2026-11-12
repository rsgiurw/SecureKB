import React from 'react';
import { Download, Eye, Plus } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
    Tooltip,
    TooltipContent,
    TooltipProvider,
    TooltipTrigger,
} from "@/components/ui/tooltip";
import { useMenuMessages } from '../../i18n/useMenuLocale';

import type { ChatMessage } from '../../types';

interface ChatHeaderProps {
    sessionId: string;
    messages: ChatMessage[];
    showDevTools: boolean;
    setShowDevTools: (show: boolean) => void;
    onNewSession: () => void;
}

export const ChatHeader: React.FC<ChatHeaderProps> = ({
    sessionId,
    messages,
    showDevTools,
    setShowDevTools,
    onNewSession,
}) => {
    // i18n: 별칭(i18n) 사용 — messages prop과의 충돌 방지
    const { messages: i18n } = useMenuMessages();
    void sessionId; // [FIX 设计评审 P1] 会话 UUID 不再展示给最终用户（会话管理在左栏）

    return (
        <header className="px-6 py-4 border-b bg-gradient-to-r from-background to-muted/10 relative overflow-hidden backdrop-blur-sm">
            <div className="flex justify-between items-center relative z-10">
                <div className="flex flex-col">
                    {/* [FIX 设计评审 P1] 品牌名不再重复：侧栏/顶栏已有 SecureKB wordmark，
                        聊天头只显示功能名"智能问答" */}
                    <h1 className="text-xl font-bold tracking-tight leading-tight">{i18n.chat.header.title}</h1>
                    <p className="text-sm text-muted-foreground">{i18n.chat.header.subtitle}</p>
                </div>

                <div className="flex items-center gap-2">

                    {!showDevTools && (
                        <TooltipProvider>
                            <Tooltip>
                                <TooltipTrigger asChild>
                                    <Button
                                        variant="ghost"
                                        size="icon"
                                        onClick={() => setShowDevTools(true)}
                                        className="rounded-xl hover:bg-muted transition-all duration-200 shrink-0"
                                        title={i18n.chat.header.showDevTools}
                                    >
                                        <Eye className="w-5 h-5" />
                                    </Button>
                                </TooltipTrigger>
                                <TooltipContent className="glass-morphism border-border/40 text-foreground font-bold text-xs rounded-xl px-3 py-1.5">
                                    {i18n.chat.header.showDevTools}
                                </TooltipContent>
                            </Tooltip>
                        </TooltipProvider>
                    )}

                    {/* [MOD EXP-021] 会话导出：当前消息转 Markdown 下载 */}
                    <TooltipProvider>
                        <Tooltip>
                            <TooltipTrigger asChild>
                                <Button
                                    variant="ghost"
                                    size="icon"
                                    disabled={messages.length === 0}
                                    onClick={() => {
                                        const lines = ['# 聊天记录导出', '']
                                        for (const m of messages) {
                                            lines.push(`**${m.role === 'user' ? '我' : '助手'}**（${m.timestamp}）：`)
                                            lines.push('')
                                            lines.push(m.content)
                                            lines.push('')
                                        }
                                        const blob = new Blob([lines.join('\n')], { type: 'text/markdown; charset=utf-8' })
                                        const url = window.URL.createObjectURL(blob)
                                        const a = globalThis.document.createElement('a')
                                        a.href = url
                                        a.download = `会话_${new Date().toISOString().slice(0, 10)}.md`
                                        a.click()
                                        window.URL.revokeObjectURL(url)
                                    }}
                                    className="rounded-xl hover:bg-muted transition-all duration-200 shrink-0"
                                    title="导出会话"
                                >
                                    <Download className="w-5 h-5" />
                                </Button>
                            </TooltipTrigger>
                            <TooltipContent className="glass-morphism border-border/40 text-foreground font-bold text-xs rounded-xl px-3 py-1.5">
                                导出会话
                            </TooltipContent>
                        </Tooltip>
                    </TooltipProvider>

                    <TooltipProvider>
                        <Tooltip>
                            <TooltipTrigger asChild>
                                <Button
                                    variant="ghost"
                                    size="icon"
                                    onClick={onNewSession}
                                    // [FIX 设计评审 P1] 新会话是创建动作：Plus 图标 + 中性 hover
                                    //（原 Trash2 + destructive 红色 hover 是删除语义，吓人且误导）
                                    className="rounded-xl hover:bg-muted transition-all duration-300 shrink-0"
                                    title={i18n.chat.header.newSession}
                                >
                                    <Plus className="w-5 h-5" />
                                </Button>
                            </TooltipTrigger>
                            <TooltipContent className="glass-morphism border-border/40 text-foreground font-bold text-xs rounded-xl px-3 py-1.5">
                                {i18n.chat.header.newSession}
                            </TooltipContent>
                        </Tooltip>
                    </TooltipProvider>
                </div>
            </div>
        </header>
    );
};
