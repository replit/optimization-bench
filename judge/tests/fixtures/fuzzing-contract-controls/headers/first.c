#include OPTIMBENCH_CONTROL_HEADER
int second_consumer(void);
int main(void)
{
    int (*volatile value)(void) = &contract_value;
    return value() + second_consumer() == 14 ? 0 : 1;
}
