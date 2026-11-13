/**
 * 문서 관리 탭 오케스트레이터
 *
 * 3개 훅(목록/선택/삭제) + 7개 하위 컴포넌트를 조합합니다.
 * 비즈니스 로직은 훅에, UI는 하위 컴포넌트에 위임하며
 * 이 파일은 조합과 배치만 담당합니다.
 */
import React, { useMemo, useState } from 'react';
import { Search, RotateCw, AlertCircle, ChevronLeft, ChevronRight } from 'lucide-react';
import { Document, ToastMessage } from '../types';
import { documentAPI } from '../services/api';
import { Card } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { useMenuMessages } from '../i18n/useMenuLocale';
import type { MenuMessages } from '../i18n/menuMessages';
import { useDocumentList, useDocumentSelection, useDocumentDelete } from '../hooks/document';
import {
  DocumentToolbar, DocumentListView, DocumentGridView, DocumentDetailDialog,
  DocumentDeleteDialog, DocumentBulkDeleteDialog, DocumentDeleteAllDialog,
} from './document';

interface DocumentsTabProps {
  showToast: (message: Omit<ToastMessage, 'id'>) => void;
}

/** 에러 상태 표시 */
const ErrorState: React.FC<{ onRetry: () => void; messages: MenuMessages }> = ({ onRetry, messages }) => (
  <Card className="border-destructive/30 bg-destructive/5 py-16 flex flex-col items-center justify-center text-center gap-4 px-6">
    <div className="w-16 h-16 rounded-full bg-destructive/10 flex items-center justify-center mb-2">
      <AlertCircle className="w-8 h-8 text-destructive" />
    </div>
    <p className="text-lg font-black text-foreground">{messages.documentsTab.errorTitle}</p>
    <p className="text-sm text-muted-foreground max-w-xs">{messages.documentsTab.errorDescription}</p>
    <Button onClick={onRetry} variant="default" className="gap-2 font-bold px-6 rounded-xl shadow-lg shadow-primary/20">
      <RotateCw className="w-4 h-4" />{messages.documentsTab.retry}
    </Button>
  </Card>
);

/** 로딩 상태 표시 */
const LoadingState: React.FC<{ messages: MenuMessages }> = ({ messages }) => (
  <div className="flex flex-col items-center justify-center py-20 gap-4">
    <RotateCw className="w-10 h-10 text-primary animate-spin opacity-20" />
    <p className="text-sm font-bold text-muted-foreground animate-pulse">{messages.documentsTab.loading}</p>
  </div>
);

/** 빈 목록 상태 표시 */
const EmptyState: React.FC<{ messages: MenuMessages }> = ({ messages }) => (
  <Card className="border-dashed border-2 py-20 flex flex-col items-center justify-center text-center">
    <div className="w-20 h-20 rounded-full bg-muted/30 flex items-center justify-center mb-4">
      <Search className="w-10 h-10 text-muted-foreground/40" />
    </div>
    <p className="text-lg font-black text-foreground">{messages.documentsTab.emptyTitle}</p>
    <p className="text-sm text-muted-foreground">{messages.documentsTab.emptyDescription}</p>
  </Card>
);

/** 페이지네이션 컨트롤 */
const Pagination: React.FC<{ page: number; total: number; onChange: (p: number) => void }> = ({ page, total, onChange }) => {
  const btn = "rounded-xl h-9 w-9 border-border/60";
  return (
    <div className="flex items-center justify-center gap-2 py-8">
      <Button variant="outline" size="icon" disabled={page === 1} onClick={() => onChange(1)} className={btn}>
        <ChevronLeft className="w-4 h-4 mr-[-1px]" /><ChevronLeft className="w-4 h-4 ml-[-1px]" />
      </Button>
      <Button variant="outline" size="icon" disabled={page === 1} onClick={() => onChange(page - 1)} className={btn}>
        <ChevronLeft className="w-4 h-4" />
      </Button>
      <div className="flex items-center gap-1 mx-2">
        <span className="text-sm font-black">Page {page}</span>
        <span className="text-sm text-muted-foreground font-bold">of {total}</span>
      </div>
      <Button variant="outline" size="icon" disabled={page === total} onClick={() => onChange(page + 1)} className={btn}>
        <ChevronRight className="w-4 h-4" />
      </Button>
      <Button variant="outline" size="icon" disabled={page === total} onClick={() => onChange(total)} className={btn}>
        <ChevronRight className="w-4 h-4 mr-[-1px]" /><ChevronRight className="w-4 h-4 ml-[-1px]" />
      </Button>
    </div>
  );
};

/** 문서 다운로드 (브라우저 DOM API 사용) */
const downloadDocument = async (doc: Document, showToast: DocumentsTabProps['showToast'], failureMessage: string) => {
  try {
    const resp = await documentAPI.downloadDocument(doc.id);
    const url = window.URL.createObjectURL(new Blob([resp.data]));
    const a = globalThis.document.createElement('a');
    a.href = url; a.download = doc.originalName || doc.filename; a.click();
    window.URL.revokeObjectURL(url);
  } catch { showToast({ type: 'error', message: failureMessage }); }
};

export const DocumentsTab: React.FC<DocumentsTabProps> = ({ showToast }) => {
  const { messages } = useMenuMessages();
  const list = useDocumentList({ showToast });
  const selection = useDocumentSelection({ showToast });
  const deletion = useDocumentDelete({ showToast, onDeleted: list.fetchDocuments, clearSelection: selection.clearSelection });
  const download = (doc: Document) => downloadDocument(doc, showToast, messages.documentsTab.downloadFailed);
  // [FIX 用户反馈] 统一链接查看：点文件名 → 新标签页打开文件（浏览器原生渲染：
  // md/txt 显示文本、PDF 原生预览、DOCX 等自动下载）。blob URL 继承页面 origin，
  // 鉴权经请求头携带、令牌不暴露 URL。
  const openInTab = async (doc: Document) => {
    try {
      const resp = await documentAPI.downloadDocument(doc.id);
      const blob = resp.data as Blob;
      const contentType = String(resp.headers?.['content-type'] || '').toLowerCase();
      const filename = String(doc.originalName || doc.filename || '').toLowerCase();
      let outBlob = blob;
      if (filename.endsWith('.docx') || contentType.includes('officedocument.wordprocessingml')) {
        // [FIX 用户反馈] DOCX 预览：mammoth 转 HTML（样式内联），浏览器直接查看
        // 而非下载。转换失败回退原 blob（浏览器下载行为）。
        try {
          const mammoth = (await import('mammoth')).default;
          const arrayBuffer = await blob.arrayBuffer();
          const { value: html } = await mammoth.convertToHtml(
            { arrayBuffer },
            { styleMap: ["p[style-name='Title'] => h1:fresh", "p[style-name='Heading 1'] => h1:fresh"] },
          );
          const styled = `<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8"><title>${filename}</title>`
            + '<style>body{max-width:820px;margin:40px auto;padding:0 24px;font-family:sans-serif;line-height:1.7}'
            // eslint-disable-next-line no-restricted-syntax -- DOCX 预览页是独立新标签 HTML（非 React 组件），不适用 COLORS token 规则
            + 'table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:6px 10px}'
            + 'img{max-width:100%}</style></head><body>' + html + '</body></html>';
          outBlob = new Blob([styled], { type: 'text/html; charset=utf-8' });
        } catch { outBlob = blob; }
      } else if (contentType.startsWith('text/')) {
        // [FIX 用户反馈] 文本乱码修复：blob.type 无 charset 时浏览器按系统
        // locale（中文 Windows = GBK）解码 UTF-8 字节 → 乱码。显式重构造
        // text/plain; charset=utf-8 的 Blob 强制按 UTF-8 渲染。
        // 统一 text/plain（含 .html 上传文件——保持 text/html 会执行其中
        // 脚本，XSS 洞；纯文本显示源码安全且全格式风格统一）。
        const text = await blob.text();
        outBlob = new Blob([text], { type: 'text/plain; charset=utf-8' });
      }
      const url = window.URL.createObjectURL(outBlob);
      window.open(url, '_blank', 'noopener');
      setTimeout(() => window.URL.revokeObjectURL(url), 60_000);
    } catch { showToast({ type: 'error', message: messages.documentsTab.downloadFailed }); }
  };
  // [FIX 用户报告] 员工只读文档中心：隐藏删除/批量删除入口（硬保障在服务端 403）
  const isAdminUser = useMemo(() => {
    try {
      return JSON.parse(localStorage.getItem('securekb_user') || 'null')?.role === 'admin';
    } catch {
      return false;
    }
  }, []);
  const readOnly = !isAdminUser;
  // [MOD EXP-024] 部门筛选（文档列表按归属过滤）
  const [deptFilter, setDeptFilter] = useState('');
  // 部门选项动态收集（从当前列表）
  const deptFilterOptions = useMemo(() => {
    const set = new Set<string>();
    for (const d of list.documents) {
      for (const dept of d.departments || []) set.add(dept);
    }
    return Array.from(set).sort();
  }, [list.documents]);

  // 콘텐츠 영역: 에러 → 로딩 → 빈 목록 → 문서 뷰
  const renderContent = () => {
    if (list.fetchError) return <ErrorState onRetry={list.fetchDocuments} messages={messages} />;
    if (list.loading) return <LoadingState messages={messages} />;
    if (list.documents.length === 0) return <EmptyState messages={messages} />;
    // [MOD EXP-024] 部门筛选（前端过滤 departments）
    const visibleDocs = deptFilter
      ? list.documents.filter((d) => (d.departments || []).includes(deptFilter))
      : list.documents;
    if (visibleDocs.length === 0) return <EmptyState messages={messages} />;
    const shared = { documents: visibleDocs, selectedDocuments: selection.selectedDocuments,
      onToggleSelect: selection.toggleSelect, onViewDetails: selection.viewDetails, onDownload: download, onOpenInTab: openInTab, onDeleteSingle: deletion.handleDeleteSingle, readOnly };
    return (<>
      {list.viewMode === 'list'
        ? <DocumentListView {...shared} onToggleSelectAll={() => selection.toggleSelectAll(list.documents)} />
        : <DocumentGridView {...shared} />}
      <Pagination page={list.page} total={list.totalPages} onChange={list.setPage} />
    </>);
  };

  return (
    <div className="space-y-6">
      <DocumentToolbar searchQuery={list.searchQuery} sortField={list.sortField} sortDirection={list.sortDirection}
        viewMode={list.viewMode} loading={list.loading} selectedCount={selection.selectedDocuments.size}
        onSearchChange={list.handleSearch} onSortFieldChange={list.handleSort} onSortDirectionToggle={list.handleSortDirection}
        onViewModeChange={list.setViewMode} onRefresh={list.fetchDocuments} onBulkDelete={deletion.handleDeleteBulk} onDeleteAll={deletion.handleDeleteAll} readOnly={readOnly}
        deptFilterOptions={deptFilterOptions} deptFilterValue={deptFilter} onDeptFilterChange={setDeptFilter} />
      {renderContent()}
      <DocumentDetailDialog
        open={selection.detailsOpen}
        document={selection.selectedDocument}
        onClose={selection.closeDetails}
        onDepartmentsSaved={() => list.fetchDocuments()}
      />
      <DocumentDeleteDialog open={deletion.deleteConfirmOpen} loading={deletion.deleteLoading} onConfirm={deletion.confirmDeleteSingle} onCancel={deletion.handleDeleteCancel} />
      <DocumentBulkDeleteDialog open={deletion.bulkDeleteConfirmOpen} loading={deletion.bulkDeleteLoading}
        selectedCount={selection.selectedDocuments.size} onConfirm={() => deletion.confirmDeleteBulk(selection.selectedDocuments)} onCancel={deletion.handleBulkDeleteCancel} />
      <DocumentDeleteAllDialog open={deletion.deleteAllConfirmOpen} loading={deletion.deleteAllLoading} step={deletion.deleteAllStep}
        typingValue={deletion.deleteAllTyping} onConfirm={deletion.confirmDeleteAll} onCancel={deletion.handleDeleteAllCancel} onTypingChange={deletion.setDeleteAllTyping} />
    </div>
  );
};
