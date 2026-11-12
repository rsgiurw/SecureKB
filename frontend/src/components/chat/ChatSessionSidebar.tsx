import { useEffect, useMemo, useRef, useState } from 'react';
import { MessageSquare, Pencil, Plus, Trash2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { cn } from '@/lib/utils';
import type { ChatMessage } from '../../types';
import { useMenuMessages } from '../../i18n/useMenuLocale';
import { format, formatDate } from '../../i18n/format';

interface StoredChatSession {
  id: string;
  title: string;
  updatedAt: string;
  messageCount: number;
  // [FIX 用户报告 IDOR] 会话归属：登录用户名——侧栏只显示当前用户的会话
  owner?: string | undefined;
}

interface ChatSessionSidebarProps {
  sessionId: string;
  messages: ChatMessage[];
  onNewSession: () => Promise<void> | void;
  onSelectSession: (sessionId: string) => Promise<void> | void;
}

const STORAGE_KEY = 'onerag_chat_sessions';

function readSessions(): StoredChatSession[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    // [FIX 用户报告 IDOR] 会话隔离：只显示当前登录用户的会话（换账户登录后
    // 旧 localStorage 列表不再互通）。无 owner 的旧条目不显示（安全优先）。
    const currentOwner = (() => {
      try {
        return JSON.parse(localStorage.getItem('securekb_user') || 'null')?.username || null;
      } catch {
        return null;
      }
    })();
    if (!currentOwner) return [];
    return parsed.filter((s) => s.owner === currentOwner);
  } catch {
    return [];
  }
}

function writeSessions(sessions: StoredChatSession[]) {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(sessions.slice(0, 50)));
}

function createDefaultTitle(messages: ChatMessage[], defaultTitle: string) {
  const firstUserMessage = messages.find((message) => message.role === 'user' && message.content?.trim());
  if (!firstUserMessage) return defaultTitle;
  const title = firstUserMessage.content.trim().replace(/\s+/g, ' ');
  return title.length > 28 ? `${title.slice(0, 28)}...` : title;
}

export function ChatSessionSidebar({ sessionId, messages, onNewSession, onSelectSession }: ChatSessionSidebarProps) {
  // i18n: prop의 messages(채팅 메시지 배열)와 이름 충돌을 피하기 위해 별칭(i18n)으로 받는다.
  const { messages: i18n, locale } = useMenuMessages();
  const [sessions, setSessions] = useState<StoredChatSession[]>([]);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingTitle, setEditingTitle] = useState('');
  const deletedSessionIdsRef = useRef<Set<string>>(new Set());
  const sessionsHydratedRef = useRef(false);
  // sessions 镜像（供 touch 判定 effect 读取当前列表，且不进依赖数组造成循环）
  const sessionsRef = useRef<StoredChatSession[]>([]);
  // [FIX 用户报告 IDOR] 当前登录用户名（会话归属标记，侧栏按此过滤）
  const currentOwnerRef = useRef<string | null>(null);
  useEffect(() => {
    sessionsRef.current = sessions;
  }, [sessions]);
  // [FIX 用户报告] 追踪 sessionId 变化：切换/新建会话时加载历史也会让 messages
  // 变化，但"查看"不应被视为"活跃"——只有同一会话内消息发生新增时才更新
  // updatedAt 并置顶，否则点开旧会话它就跳到列表顶部（用户尚未提问）。
  const lastTouchedSessionRef = useRef<string | null>(null);
  const lastMessageCountRef = useRef<number>(0);

  useEffect(() => {
    setSessions(readSessions());
    // [FIX 用户报告 IDOR] 挂载时读当前登录用户名（归属标记用）
    try {
      currentOwnerRef.current = JSON.parse(localStorage.getItem('securekb_user') || 'null')?.username || null;
    } catch {
      currentOwnerRef.current = null;
    }
  }, []);

  // localStorage 영속화는 상태 업데이터(순수해야 하며 StrictMode에서 이중 호출됨)와
  // 분리해 전용 effect에서 수행한다([41]/[47]).
  useEffect(() => {
    // 최초 마운트의 빈 초기값으로 저장소를 덮어쓰지 않도록 첫 렌더는 건너뛴다.
    if (!sessionsHydratedRef.current) {
      sessionsHydratedRef.current = true;
      return;
    }
    writeSessions(sessions);
  }, [sessions]);

  useEffect(() => {
    if (!sessionId) return;
    if (deletedSessionIdsRef.current.has(sessionId)) return;

    // [FIX 用户报告] "查看≠活跃"：判定本次 effect 的触发原因。
    // - 切换/新建会话（sessionId 与上次不同）：加载历史会让 messages 变化，
    //   但不应更新 updatedAt、不应置顶——用户只是查看，还没提问。
    // - 同一会话内消息数增加：真实对话发生 → touch updatedAt 并置顶。
    // v2 修复（用户复测仍置顶）：仅靠 sessionId 变化判定不够——切换后"清空→
    // 历史填充"是两次独立提交，第二轮 (同sessionId, 历史N条) 会被误判为新增。
    // 追加可靠信号：本地新消息 id 以 'msg-' 开头（createClientId），
    // 后端历史消息 id 是数字/时间戳/'role-index' 形态——用最后一条消息的
    // id 前缀区分"历史填充"与"真实新消息"。
    // refs 全部在 updater 外更新（updater 保持纯函数，StrictMode 双调用安全）。
    const previousSessions = sessionsRef.current;
    const sessionChanged = lastTouchedSessionRef.current !== sessionId;
    const prevCount = lastTouchedSessionRef.current === sessionId ? lastMessageCountRef.current : -1;
    const lastMessage = messages[messages.length - 1];
    const lastMessageIsLocal = !!lastMessage && String(lastMessage.id).startsWith('msg-');
    const isRealActivity =
      !sessionChanged && messages.length > prevCount && prevCount >= 0 && lastMessageIsLocal;

    lastTouchedSessionRef.current = sessionId;
    lastMessageCountRef.current = messages.length;

    if (!isRealActivity) {
      // 查看/历史加载：仅确保条目存在（新建会话首轮消息走 isRealActivity 正常路径），
      // 不改 updatedAt、不重排。
      if (sessionChanged && !previousSessions.some((session) => session.id === sessionId) && messages.length > 0) {
        // 从后端历史恢复了一个列表里没有的会话（如清缓存后）→ 以"较旧"身份补录，
        // 不抢置顶：updatedAt 用消息数占位为最小值会破坏排序语义，这里按现有最旧条目
        // 之前插入。简化：直接以当前时间补录（首次出现，用户无置顶预期差异）。
        const now = new Date().toISOString();
        const recovered: StoredChatSession = {
          id: sessionId,
          title: createDefaultTitle(messages, i18n.chatSidebar.defaultTitle),
          updatedAt: now,
          messageCount: messages.length,
          owner: currentOwnerRef.current ?? undefined,
        };
        const next = [recovered, ...previousSessions].sort(
          (a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime()
        );
        setSessions(next);
      }
      return;
    }

    setSessions((previous) => {
      const existing = previous.find((session) => session.id === sessionId);

      const now = new Date().toISOString();
      const nextSession: StoredChatSession = {
        id: sessionId,
        title: existing?.title && existing.title !== i18n.chatSidebar.defaultTitle
          ? existing.title
          : createDefaultTitle(messages, i18n.chatSidebar.defaultTitle),
        updatedAt: now,
        messageCount: messages.length,
        owner: currentOwnerRef.current ?? undefined,
      };

      const next = [
        nextSession,
        ...previous.filter((session) => session.id !== sessionId),
      ].sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime());

      // 영속화는 위의 전용 effect가 담당한다(updater는 순수 유지).
      return next;
    });
  }, [sessionId, messages, i18n.chatSidebar.defaultTitle]);

  const sortedSessions = useMemo(
    () => [...sessions].sort((a, b) => new Date(b.updatedAt).getTime() - new Date(a.updatedAt).getTime()),
    [sessions]
  );

  const handleSelectSession = (targetSessionId: string) => {
    if (targetSessionId === sessionId) return;
    onSelectSession(targetSessionId);
  };

  const handleDeleteSession = (targetSessionId: string) => {
    deletedSessionIdsRef.current.add(targetSessionId);
    const next = sessions.filter((session) => session.id !== targetSessionId);
    setSessions(next);  // 영속화는 전용 effect가 처리

    if (targetSessionId === sessionId) {
      localStorage.removeItem('chatSessionId');
      onNewSession();
    }
  };

  const startEditing = (session: StoredChatSession) => {
    setEditingId(session.id);
    setEditingTitle(session.title);
  };

  const saveTitle = () => {
    if (!editingId) return;
    const next = sessions.map((session) =>
      session.id === editingId
        ? { ...session, title: editingTitle.trim() || i18n.chatSidebar.defaultTitle }
        : session
    );
    setSessions(next);  // 영속화는 전용 effect가 처리
    setEditingId(null);
    setEditingTitle('');
  };

  return (
    <aside className="hidden lg:flex w-72 shrink-0 flex-col border-r border-border/60 bg-background/80 backdrop-blur-sm">
      <div className="p-4 border-b border-border/60 space-y-3">
        <div className="flex items-center justify-between gap-2">
          <div>
            <h2 className="text-sm font-bold tracking-tight">{i18n.chatSidebar.heading}</h2>
            <p className="text-xs text-muted-foreground">{i18n.chatSidebar.subtitle}</p>
          </div>
          <Button size="icon" variant="outline" className="rounded-xl" onClick={onNewSession} title={i18n.chatSidebar.newChat}>
            <Plus className="w-4 h-4" />
          </Button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {sortedSessions.length === 0 ? (
          <div className="flex flex-col items-center justify-center text-center text-muted-foreground h-40 gap-2">
            <MessageSquare className="w-8 h-8 opacity-50" />
            <p className="text-sm">{i18n.chatSidebar.emptyState}</p>
          </div>
        ) : (
          sortedSessions.map((session) => {
            const isActive = session.id === sessionId;
            const isEditing = editingId === session.id;

            return (
              <div
                key={session.id}
                className={cn(
                  'group rounded-2xl border p-3 transition-all cursor-pointer',
                  isActive
                    ? 'border-primary/40 bg-primary/10 shadow-sm'
                    : 'border-border/50 bg-card/40 hover:bg-muted/60'
                )}
                onClick={() => !isEditing && handleSelectSession(session.id)}
              >
                <div className="flex items-start gap-2">
                  <MessageSquare className="w-4 h-4 mt-0.5 text-primary/70 shrink-0" />
                  <div className="min-w-0 flex-1">
                    {isEditing ? (
                      <Input
                        value={editingTitle}
                        onChange={(event) => setEditingTitle(event.target.value)}
                        onBlur={saveTitle}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter') saveTitle();
                          if (event.key === 'Escape') setEditingId(null);
                        }}
                        autoFocus
                        className="h-8 text-sm rounded-xl"
                        onClick={(event) => event.stopPropagation()}
                      />
                    ) : (
                      <p className="truncate text-sm font-semibold text-foreground">{session.title}</p>
                    )}
                    <p className="mt-1 text-xs text-muted-foreground">
                      {format(i18n.chatSidebar.messageCount, { count: session.messageCount })} · {formatDate(session.updatedAt, locale)}
                    </p>
                  </div>
                  <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                    <Button
                      size="icon"
                      variant="ghost"
                      className="h-7 w-7 rounded-lg"
                      onClick={(event) => {
                        event.stopPropagation();
                        startEditing(session);
                      }}
                      title={i18n.chatSidebar.rename}
                    >
                      <Pencil className="w-3.5 h-3.5" />
                    </Button>
                    <Button
                      size="icon"
                      variant="ghost"
                      className="h-7 w-7 rounded-lg hover:text-destructive"
                      onClick={(event) => {
                        event.stopPropagation();
                        handleDeleteSession(session.id);
                      }}
                      title={i18n.chatSidebar.delete}
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </Button>
                  </div>
                </div>
              </div>
            );
          })
        )}
      </div>
    </aside>
  );
}
