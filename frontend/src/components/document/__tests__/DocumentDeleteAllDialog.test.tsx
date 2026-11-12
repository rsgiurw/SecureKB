/**
 * DocumentDeleteAllDialog 컴포넌트 테스트
 *
 * 全部删除 2단계 확인 다이얼로그를 검증합니다.
 * 1단계(confirm): "네, 정말 모두 삭제합니다" 버튼
 * 2단계(typing): 확인 문구 입력 후 "全部删除 실행" 버튼
 */
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { DocumentDeleteAllDialog } from '../DocumentDeleteAllDialog';

describe('DocumentDeleteAllDialog', () => {
  const defaultProps = {
    open: true,
    loading: false,
    step: 'confirm' as const,
    typingValue: '',
    onConfirm: vi.fn(),
    onCancel: vi.fn(),
    onTypingChange: vi.fn(),
  };

  it('다이얼로그 제목을 렌더링합니다', () => {
    render(<DocumentDeleteAllDialog {...defaultProps} />);
    expect(screen.getByText('删除所有文档')).toBeInTheDocument();
  });

  it('위험 경고 메시지를 표시합니다', () => {
    render(<DocumentDeleteAllDialog {...defaultProps} />);
    expect(screen.getByText(/所有文档数据将被永久删除/)).toBeInTheDocument();
  });

  it('confirm 단계에서 "네, 정말 모두 삭제합니다" 버튼을 표시합니다', () => {
    render(<DocumentDeleteAllDialog {...defaultProps} step="confirm" />);
    expect(screen.getByText('是的，全部删除')).toBeInTheDocument();
  });

  it('confirm 단계에서 확인 버튼 클릭 시 onConfirm을 호출합니다', () => {
    const onConfirm = vi.fn();
    render(<DocumentDeleteAllDialog {...defaultProps} onConfirm={onConfirm} step="confirm" />);
    fireEvent.click(screen.getByText('是的，全部删除'));
    expect(onConfirm).toHaveBeenCalled();
  });

  it('typing 단계에서 입력 필드를 표시합니다', () => {
    render(<DocumentDeleteAllDialog {...defaultProps} step="typing" />);
    expect(screen.getByPlaceholderText('请输入短语')).toBeInTheDocument();
  });

  it('typing 단계에서 "全部删除 실행" 버튼을 표시합니다', () => {
    render(<DocumentDeleteAllDialog {...defaultProps} step="typing" />);
    expect(screen.getByText('执行全部删除')).toBeInTheDocument();
  });

  it('typing 단계에서 올바른 문구를 입력하지 않으면 버튼이 비활성화됩니다', () => {
    render(<DocumentDeleteAllDialog {...defaultProps} step="typing" typingValue="틀린 문구" />);
    expect(screen.getByText('执行全部删除')).toBeDisabled();
  });

  it('typing 단계에서 올바른 문구를 입력하면 버튼이 활성화됩니다', () => {
    render(<DocumentDeleteAllDialog {...defaultProps} step="typing" typingValue="I agree to delete the documents." />);
    expect(screen.getByText('执行全部删除')).not.toBeDisabled();
  });

  it('typing 단계에서 입력 시 onTypingChange를 호출합니다', () => {
    const onTypingChange = vi.fn();
    render(<DocumentDeleteAllDialog {...defaultProps} step="typing" onTypingChange={onTypingChange} />);
    const input = screen.getByPlaceholderText('请输入短语');
    fireEvent.change(input, { target: { value: '문서 삭' } });
    expect(onTypingChange).toHaveBeenCalledWith('문서 삭');
  });

  it('취소 버튼 클릭 시 onCancel을 호출합니다', () => {
    const onCancel = vi.fn();
    render(<DocumentDeleteAllDialog {...defaultProps} onCancel={onCancel} />);
    fireEvent.click(screen.getByText('停止并返回'));
    expect(onCancel).toHaveBeenCalled();
  });

  it('로딩 중에는 확인 버튼이 비활성화됩니다', () => {
    render(<DocumentDeleteAllDialog {...defaultProps} loading={true} step="confirm" />);
    expect(screen.getByText('是的，全部删除')).toBeDisabled();
  });

  it('로딩 중에는 취소 버튼이 비활성화됩니다', () => {
    render(<DocumentDeleteAllDialog {...defaultProps} loading={true} />);
    expect(screen.getByText('停止并返回')).toBeDisabled();
  });
});
