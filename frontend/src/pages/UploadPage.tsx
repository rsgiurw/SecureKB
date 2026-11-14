import { useMemo, useState } from 'react';
import {
  UploadCloud,
  FileText,
  Settings,
} from 'lucide-react';
import { UploadTab } from '../components/UploadTab';
import { DocumentsTab } from '../components/DocumentsTab';
import { ChatSettingsManager } from '../components/ChatSettingsManager';
import { ToastMessage } from '../types';
import { useToast } from '@/hooks/use-toast';
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useMenuMessages } from '../i18n/useMenuLocale';
import { useIsFeatureEnabled } from '../core/useFeature';

/**
 * [FIX 用户报告] 文档页角色分层：
 * - 管理员：上传 / 文档管理 / 聊天机器人设置 三 tab（原样）
 * - 普通员工：只读"文档中心"——仅文档列表 tab（服务端列表/下载按部门可见性过滤，
 *   上传/删除 API 仍仅管理员）；未登录不进页（App 层登录门已兜底）。
 * 原 ProtectedRoute（员工重定向回问答页）移除——它是"员工不能看文档"的根因。
 * [FIX 2026-11-14] 运行设置·功能开关的"文档上传"开关此前是摆设（零消费方）——
 * 现接入 features.documentManagement.upload：关闭后上传 tab 隐藏、自动落到文档管理。
 */
export default function UploadPage() {
  const [activeTab, setActiveTab] = useState("upload");
  const { toast } = useToast();
  const { messages } = useMenuMessages();
  const uploadEnabled = useIsFeatureEnabled('documentManagement', 'upload');

  const isAdminUser = useMemo(() => {
    try {
      return JSON.parse(localStorage.getItem('securekb_user') || 'null')?.role === 'admin';
    } catch {
      return false;
    }
  }, []);

  // 토스트 메시지 표시
  const showToast = (message: Omit<ToastMessage, 'id'>) => {
    toast({
      variant: message.type === 'error' ? 'destructive' : 'default',
      title: message.type === 'success' ? messages.common.toastSuccess : message.type === 'error' ? messages.common.toastError : messages.common.toastInfo,
      description: message.message,
    });
  };

  // 员工只读视图：默认落在文档列表 tab；上传开关关闭时管理员也强制落文档管理
  const canShowUpload = isAdminUser && uploadEnabled;
  const effectiveTab = canShowUpload ? activeTab : 'documents';

  return (
    <div className="container max-w-7xl mx-auto py-6 space-y-6">
      <Tabs value={effectiveTab} onValueChange={setActiveTab} className="w-full">
        <div className="border-b border-border/40 bg-background/50 backdrop-blur-sm sticky top-16 z-30 -mx-4 px-4 md:mx-0 md:px-0 rounded-t-2xl">
          <TabsList className="h-14 w-full justify-start bg-transparent p-0 gap-2">
            {canShowUpload && (
              <TabsTrigger
                value="upload"
                className="h-14 data-[state=active]:bg-transparent data-[state=active]:shadow-none data-[state=active]:border-b-2 data-[state=active]:border-primary rounded-none px-6 gap-2 font-bold transition-all hover:bg-muted/50"
              >
                <UploadCloud className="w-5 h-5" />
                {messages.pages.documentUploadTab}
              </TabsTrigger>
            )}
            <TabsTrigger
              value="documents"
              className="h-14 data-[state=active]:bg-transparent data-[state=active]:shadow-none data-[state=active]:border-b-2 data-[state=active]:border-primary rounded-none px-6 gap-2 font-bold transition-all hover:bg-muted/50"
            >
              <FileText className="w-5 h-5" />
              {messages.pages.documentManagementTab}
            </TabsTrigger>
            {isAdminUser && (
              <TabsTrigger
                value="settings"
                className="h-14 data-[state=active]:bg-transparent data-[state=active]:shadow-none data-[state=active]:border-b-2 data-[state=active]:border-primary rounded-none px-6 gap-2 font-bold transition-all hover:bg-muted/50"
              >
                <Settings className="w-5 h-5" />
                {messages.pages.chatSettingsTab}
              </TabsTrigger>
            )}
          </TabsList>
        </div>

        <div className="mt-6">
          {canShowUpload && (
            <TabsContent value="upload" className="animate-in fade-in-50 duration-300">
              <UploadTab showToast={showToast} />
            </TabsContent>
          )}
          <TabsContent value="documents" className="animate-in fade-in-50 duration-300">
            <DocumentsTab showToast={showToast} />
          </TabsContent>
          {isAdminUser && (
            <TabsContent value="settings" className="animate-in fade-in-50 duration-300">
              <ChatSettingsManager
                onSave={() => {
                  showToast({
                    type: 'success',
                    message: messages.pages.chatSettingsSaved,
                  });
                }}
              />
            </TabsContent>
          )}
        </div>
      </Tabs>
    </div>
  );
}
