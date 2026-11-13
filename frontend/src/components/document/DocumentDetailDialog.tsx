/**
 * 문서 상세 정보 다이얼로그 컴포넌트
 *
 * 선택한 문서의 메타데이터(파일명, ID, 크기, MIME 타입, 업로드 일시,
 * 상태, 청크 수, 페이지 수, 단어 수)를 다이얼로그로 표시합니다.
 */
import React, { useEffect, useState } from 'react';
import { MoreHorizontal } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { ScrollArea } from '@/components/ui/scroll-area';
import api from '../../services/api';
import { formatFileSize } from '../../utils/documentUtils';
import { useMenuMessages } from '../../i18n/useMenuLocale';
import { format, formatDate } from '../../i18n/format';
import type { Document } from '../../types';

/** DocumentDetailDialog 컴포넌트의 Props */
export interface DocumentDetailDialogProps {
  /** 다이얼로그 열림 여부 */
  open: boolean;
  /** 상세 정보를 표시할 문서 (null이면 내용 미표시) */
  document: Document | null;
  /** 닫기 핸들러 */
  onClose: () => void;
  /** [MOD EXP-024] 部门归类保存成功回调（父组件刷新列表） */
  onDepartmentsSaved?: (documentId: string, departments: string[]) => void;
}

/**
 * 양수 카운트 타입 가드.
 *
 * React는 false/null/undefined만 렌더링에서 건너뛰고 숫자 0은 그대로 "0" 텍스트로 표시한다.
 * 따라서 `{count && <Row/>}` 형태는 count가 0일 때 화면에 길잃은 '0'을 남긴다.
 * 이 가드로 0/undefined를 모두 걸러내고, 양수일 때만 행을 렌더링한다.
 */
const hasPositiveCount = (value: number | undefined): value is number => (
  typeof value === 'number' && value > 0
);

/** 상세 정보 행 컴포넌트 (라벨 + 값) */
const DetailRow = ({ label, value, copyable }: { label: string; value: string | number | undefined | null; copyable?: boolean }) => (
  <div className="group/row">
    <p className="text-[10px] uppercase font-black text-muted-foreground/60 mb-1 tracking-wider">{label}</p>
    <div className="flex items-center justify-between p-3 rounded-xl bg-muted/30 border border-transparent group-hover/row:border-border/60 transition-all">
      <p className="text-sm font-bold text-foreground break-all">{value || '-'}</p>
      {copyable && value && (
        <Button variant="ghost" size="icon" className="h-6 w-6 opacity-0 group-hover/row:opacity-100 transition-opacity" onClick={() => navigator.clipboard.writeText(String(value))}>
          <MoreHorizontal className="w-3 h-3" />
        </Button>
      )}
    </div>
  </div>
);

/**
 * 문서 상세 정보 다이얼로그 컴포넌트
 *
 * 문서의 메타데이터와 상태 정보를 스크롤 가능한 영역에 행(row) 형태로 표시합니다.
 */
export const DocumentDetailDialog: React.FC<DocumentDetailDialogProps> = ({
  open,
  document: doc,
  onClose,
  onDepartmentsSaved,
}) => {
  // i18n: 현재 로케일에 해당하는 번역 사전과 로케일 코드를 가져온다.
  const { messages, locale } = useMenuMessages();
  // [MOD EXP-024] 文档部门归类编辑（管理员）：当前归属 chips + checkbox 调整
  const isAdminUser = (() => {
    try {
      return JSON.parse(localStorage.getItem('securekb_user') || 'null')?.role === 'admin';
    } catch {
      return false;
    }
  })();
  const [docDepts, setDocDepts] = useState<string[]>(doc?.departments ?? []);
  const [deptSaving, setDeptSaving] = useState(false);
  useEffect(() => {
    setDocDepts(doc?.departments ?? []);
  }, [doc?.id, doc?.departments]);
  const toggleDocDept = (d: string) => {
    setDocDepts((prev: string[]) => {
      if (prev.includes(d)) {
        return prev.length > 1 ? prev.filter((x) => x !== d) : prev; // 至少一个
      }
      return [...prev, d];
    });
  };
  const saveDocDepts = async () => {
    if (!doc || deptSaving) return;
    setDeptSaving(true);
    try {
      await api.patch(`/api/admin/documents/${doc.id}/departments`, { departments: docDepts });
      // [FIX] 保存成功 → 通知父组件刷新列表（badges 即时反映新归属）
      onDepartmentsSaved?.(doc.id, docDepts);
    } catch {
      setDocDepts(doc?.departments ?? []);
    } finally {
      setDeptSaving(false);
    }
  };
  // 部门选项（与用户管理同源——从部门 API 拉）
  const [allDepts, setAllDepts] = useState<string[]>([]);
  useEffect(() => {
    if (open && isAdminUser) {
      api.get('/api/admin/departments')
        .then((r) => setAllDepts(r.data.departments || []))
        .catch(() => setAllDepts(['全员', '技术部', '法务部']));
    }
  }, [open, isAdminUser]);
  // 기존 toLocaleString() 기본 출력(날짜 + 시:분:초)을 바이트 동일하게 보존하기 위한 옵션
  const uploadedAtOptions: Intl.DateTimeFormatOptions = {
    year: 'numeric', month: 'numeric', day: 'numeric',
    hour: 'numeric', minute: 'numeric', second: 'numeric',
  };
  return (
    <Dialog open={open} onOpenChange={(isOpen) => { if (!isOpen) onClose(); }}>
      <DialogContent className="max-w-md rounded-[28px] border-border/40 p-0 overflow-hidden">
        <DialogHeader className="p-6 pb-4 bg-muted/30">
          <DialogTitle className="text-xl font-black">{messages.docDetail.title}</DialogTitle>
          <DialogDescription className="text-sm font-medium">{messages.docDetail.description}</DialogDescription>
        </DialogHeader>
        <div className="p-6 pt-0 space-y-4">
          <ScrollArea className="max-h-[65vh] pr-4">
            {doc && (
              <div className="space-y-4">
                {/* [FIX 用户反馈] 统一链接查看：内容浏览改由文件名点击在新标签页打开
                    （浏览器原生渲染，多格式不再乱码）——本弹窗回归纯元数据展示 */}
                <details className="group rounded-xl border border-border/40">
                  <summary className="px-3 py-2 text-xs font-bold text-muted-foreground cursor-pointer hover:bg-muted/40 rounded-xl select-none">
                    文件信息（大小 / 类型 / 上传时间等）
                  </summary>
                  <div className="px-3 pb-3 pt-1 space-y-3">
                    <DetailRow label={messages.docDetail.filename} value={doc.originalName} />
                    <DetailRow label={messages.docDetail.documentId} value={doc.id} copyable />
                    <DetailRow label={messages.docDetail.fileSize} value={formatFileSize(doc.size)} />
                    <DetailRow label={messages.docDetail.mimeType} value={doc.mimeType} />
                    <DetailRow label={messages.docDetail.uploadedAt} value={formatDate(doc.uploadedAt, locale, uploadedAtOptions)} />
                    <DetailRow label={messages.docDetail.status} value={doc.status} />
                    {hasPositiveCount(doc.chunks) && <DetailRow label={messages.docDetail.chunkCount} value={format(messages.docDetail.chunkCountValue, { count: doc.chunks })} />}
                    {hasPositiveCount(doc.metadata?.pageCount) && <DetailRow label={messages.docDetail.pageCount} value={format(messages.docDetail.pageCountValue, { count: doc.metadata?.pageCount ?? 0 })} />}
                    {hasPositiveCount(doc.metadata?.wordCount) && <DetailRow label={messages.docDetail.wordCount} value={format(messages.docDetail.wordCountValue, { count: doc.metadata?.wordCount ?? 0 })} />}
                  </div>
                </details>

                {/* [MOD EXP-024] 文档部门归类（管理员）：调整影响检索可见范围 */}
                {isAdminUser && (
                  <div className="space-y-2">
                    <p className="text-[10px] uppercase font-black text-muted-foreground/60 tracking-wider">
                      部门归类（决定哪些部门可检索到该文档）
                    </p>
                    <div className="flex flex-wrap gap-2">
                      {allDepts.map((d) => (
                        <button
                          key={d}
                          onClick={() => toggleDocDept(d)}
                          className={`text-xs px-3 py-1.5 rounded-full border transition-all ${
                            docDepts.includes(d)
                              ? 'bg-primary text-primary-foreground border-primary font-bold'
                              : 'bg-muted/30 text-muted-foreground border-border/50 hover:border-primary/40'
                          }`}
                        >
                          {d}
                        </button>
                      ))}
                    </div>
                    <Button
                      variant="secondary"
                      size="sm"
                      className="w-full rounded-xl font-bold"
                      disabled={deptSaving || docDepts === (doc?.departments ?? [])}
                      onClick={() => void saveDocDepts()}
                    >
                      {deptSaving ? '保存中...' : '保存部门归类'}
                    </Button>
                  </div>
                )}
              </div>
            )}
          </ScrollArea>
        </div>
        <DialogFooter className="p-6 bg-muted/10">
          <Button variant="secondary" className="rounded-xl font-bold w-full" onClick={onClose}>{messages.docDetail.close}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};
