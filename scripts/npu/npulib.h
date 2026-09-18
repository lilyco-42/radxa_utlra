/****************************************************************************
*  npulib header file
****************************************************************************/
#ifndef _NPULIB_H_
#define _NPULIB_H_

#include <stdint.h>
#include <cstddef>

#ifdef __cplusplus
extern "C"{
#endif

#define MAX_NETWORK_OUTPUT  64
#define MAX_NPU_IO          64


typedef struct _npu_network  *npu_network;
typedef struct _npu_buffer   *npu_buffer;

typedef enum _save_file_type_e
{
    SAVE_NONE,
    SAVE_BINARY,
    SAVE_TEXT
} save_file_type_e;

typedef struct _output_info_s
{
    float   *ptr;
    int      length;
} output_info_s;

#define _CHECK_STATUS( stat )  do {\
    if( 0 != stat ) {\
        printf("Error: %s: %s at %d\n", __FILE__, __FUNCTION__, __LINE__);\
    }\
} while(0)



typedef struct _tensor_desc_s
{
    char       name[256];
    unsigned   num_dims;
    unsigned   sizes[8];
    unsigned   elem_bytes;
    unsigned   n_elem;          /* 元素总数 (sizes 连乘), demo 用它打印/校验 */
    unsigned   data_format;   /* VIP_BUFFER_FORMAT_* 值 (与 demo 的 kFmt* 同值) */
} tensor_desc_s;

/* VIP format code → 元素字节数 (0=FP32 1=FP16 2=UINT8 3=INT8 4=UINT16 5=INT16
   6=CHAR 7=BFP16 8=INT32 9=UINT32 10=INT64 11=UINT64 12=FP64) */
static inline unsigned vip_fmt_bytes(unsigned fmt)
{
    switch (fmt) {
        case 0: case 8: case 9:  return 4;   /* FP32 / INT32 / UINT32 */
        case 1: case 7:          return 2;   /* FP16 / BFP16 */
        case 4: case 5:          return 2;   /* UINT16 / INT16 */
        case 2: case 3: case 6:  return 1;   /* UINT8 / INT8 / CHAR */
        default:                 return 8;   /* INT64 / UINT64 / FP64 */
    }
}

class NpuUint
{
public:
    NpuUint(void);
    ~NpuUint(void);

    unsigned int get_driver_version(void);
    int npu_init(unsigned int mem_size=0);
    int query_hardware_info(void);
    int npu_destroy(void);
};


class NpuBuffer
{
public:
    NpuBuffer(void);
    ~NpuBuffer(void);

    // create a buffer
    void *create_buffer(unsigned int buffer_size);

private:
    npu_buffer m_buffer_obj;
};


class NetworkItem
{
public:
    NetworkItem(void);
    ~NetworkItem(void);

    int network_create(char *model_file, unsigned int network_id);

    // specify the memory pool buffer, should be called before prepare network
    unsigned int get_memory_pool_size(void);
    int set_memory_pool_buffer(void *memory_pool_buffer_obj);

    // only use in multi_thread demo, should be called before prepare network
    // set priority of network. 0 ~ 255, 0 indicates the lowest priority
    int set_priority(unsigned char priority);

    int network_prepare(void);
    int network_input_output_set(void);

    // get  input buffer info
    int get_network_input_buff_info(int buff_idx, void **input_buff_ptr, unsigned int *buff_size);
    // get output buffer info
    int get_network_output_buff_info(int buff_idx, void **output_buff_ptr, unsigned int *buff_size);


    // input file such as: xxx.tensor, xxx.dat, xxx.bin, xxx.txt
    int network_load_input_file(char **input_path);
    int network_load_input_file_idx(char *input_path, int input_idx);

    // one input, eg: BGR, RGB
    int network_load_input_buffer(void *input_data, unsigned int input_size);
    int network_load_input_buffer_idx(void *input_data, unsigned int input_size, int input_idx);

    // input yuv buffer
    int network_load_input_yuv_buffer(void *yuv_data, int w, int h);
    // input yuv file
    int network_load_input_yuv_file(char **input_path, int w, int h);


    int network_run(void);

    float **get_output(float **output_float=NULL, save_file_type_e save_type=SAVE_NONE);    //default: SAVE_NONE
    void get_output_fp_nocopy(output_info_s *outputs_info, save_file_type_e save_type=SAVE_NONE);

    char *get_ngb_name(void);

    int get_output_cnt(void);
    int get_input_cnt(void) { return m_input_count; }

    /* --- KWS patch: 暴露 tensor 描述符 (上层 pack/unpack 需要 sizes/format/name) --- */
    tensor_desc_s   m_input_desc[MAX_NPU_IO];
    tensor_desc_s   m_output_desc[MAX_NPU_IO];
    unsigned int    m_input_data_len[MAX_NPU_IO];

    static void fill_desc(tensor_desc_s *d, const char *name, int num_dims,
                          const unsigned *sizes, unsigned data_format)
    {
        memset(d, 0, sizeof(*d));
        if (name) { strncpy(d->name, name, sizeof(d->name) - 1); }
        d->num_dims   = (unsigned)(num_dims > 0 ? num_dims : 0);
        for (int k = 0; k < 8; ++k)
            d->sizes[k] = (k < (int)d->num_dims) ? sizes[k] : 0;
        d->data_format = data_format;
        d->elem_bytes  = vip_fmt_bytes(d->data_format);
        d->n_elem      = 1;
        for (unsigned k = 0; k < d->num_dims && k < 8; ++k) d->n_elem *= d->sizes[k];
    }
    static unsigned desc_elems(const tensor_desc_s *d)
    {
        unsigned n = 1;
        for (unsigned k = 0; k < d->num_dims && k < 8; ++k) n *= d->sizes[k];
        return n;
    }


    void network_finish(void);

    void network_destroy(void);

    npu_network     m_network;


    // output_data length
    unsigned int    m_output_data_len[MAX_NETWORK_OUTPUT] = {0}; // 32 may change

private:
    // create input/output buffer
    int network_create_io_buffer(void);

    /* network information. */
    int             m_nbg_name;
    int             m_input_count;
    int             m_output_count;

    /* NPU buffer objects. */
    npu_buffer     *m_input_buffers;
    npu_buffer     *m_output_buffers;

    void          **m_input_buffers_handle;
    void          **m_output_buffers_handle;

};



#ifdef __cplusplus
}
#endif

#endif
